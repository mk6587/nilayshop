from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx, re, json
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from typing import Optional

app = FastAPI(title="Trendyol → Persian Product Extractor")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

translator = GoogleTranslator(source="auto", target="fa")

def t(text: Optional[str]) -> str:
    if not text:
        return ""
    try:
        return translator.translate(text)
    except Exception:
        # Fallback to original if translation hits a rate limit or fails.
        return text

def fetch(url: str) -> str:
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en;q=0.8,fa;q=0.7",
    }
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text

def parse_product(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    data = {
        "image": "",
        "name": "",
        "price": "",
        "description": "",
        "brand": "",
        "category": "",
        "weight": "",
    }

    # Prefer JSON-LD Product schema if present
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            js = json.loads(tag.string.strip())
        except Exception:
            continue

        # The JSON-LD can be an array or object
        candidates = js if isinstance(js, list) else [js]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") in ["Product", "product"]:
                data["name"] = obj.get("name") or data["name"]
                images = obj.get("image")
                if isinstance(images, list) and images:
                    data["image"] = images[0]
                elif isinstance(images, str):
                    data["image"] = images
                brand = obj.get("brand")
                if isinstance(brand, dict):
                    data["brand"] = brand.get("name") or data["brand"]
                elif isinstance(brand, str):
                    data["brand"] = brand
                offers = obj.get("offers") or {}
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("highPrice") or offers.get("lowPrice")
                    currency = offers.get("priceCurrency") or ""
                    data["price"] = f"{price} {currency}".strip() if price else data["price"]
                data["description"] = obj.get("description") or data["description"]
                cats = obj.get("category") or obj.get("categoryName")
                if isinstance(cats, list):
                    data["category"] = " / ".join(cats)
                elif isinstance(cats, str):
                    data["category"] = cats
                # Weight might be hidden in additionalProperty or description; try to find
                addp = obj.get("additionalProperty") or obj.get("additionalProperties")
                if isinstance(addp, list):
                    for p in addp:
                        name = (p.get("name") or "").lower()
                        if "ağırlık" in name or "weight" in name:
                            data["weight"] = p.get("value") or p.get("unitCode") or ""
                break

    # Fallbacks if some fields missing
    if not data["image"]:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            data["image"] = og["content"]

    if not data["name"]:
        h1 = soup.find("h1")
        if h1:
            data["name"] = h1.get_text(strip=True)

    if not data["brand"]:
        # Trendyol often shows brand as <a class="product-brand">Brand</a>
        b = soup.select_one("a.product-brand")
        if b:
            data["brand"] = b.get_text(strip=True)

    if not data["price"]:
        # Try common price selectors
        p = soup.select_one('[class*="product-price"], [class*="prc-dsc"], [class*="prc-slg"]')
        if p:
            data["price"] = p.get_text(strip=True)

    if not data["description"]:
        # Try short description box
        desc = soup.select_one("#product-description, .detail-description, .description, .product-desc, .info-text")
        if desc:
            data["description"] = desc.get_text("\n", strip=True)

    if not data["category"]:
        # Breadcrumbs
        crumbs = [c.get_text(strip=True) for c in soup.select('nav.breadcrumb a, .breadcrumb a, [data-test-id="breadcrumb"] a')]
        if crumbs:
            data["category"] = " / ".join(crumbs)

    if not data["weight"]:
        # Look for Turkish weight keywords in page text
        text = soup.get_text("\n", strip=True)
        # Common patterns like "Ağırlık: 350 g" or "350 gr"
        m = re.search(r"Ağırlık[: ]+([0-9.,]+\s?(?:kg|g|gr))", text, flags=re.I)
        if not m:
            m = re.search(r"([0-9.,]+\s?(?:kg|g|gr))\s+ağırlık", text, flags=re.I)
        if m:
            data["weight"] = m.group(1)

    # Strip extra whitespace
    for k, v in list(data.items()):
        if isinstance(v, str):
            data[k] = re.sub(r"\s+", " ", v).strip()

    return data

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "result": None, "error": None})

@app.post("/extract", response_class=HTMLResponse)
async def extract(request: Request, url: str = Form(...)):
    error = None
    result = None
    try:
        html = fetch(url)
        product = parse_product(html)

        # Translate fields to Persian (fa)
        result = {
            "image": product["image"],
            "name": t(product["name"]),
            "price": product["price"],  # keep numbers/currency as-is
            "description": t(product["description"]),
            "brand": t(product["brand"]),
            "category": t(product["category"]),
            "weight": product["weight"],
        }
    except httpx.HTTPStatusError as e:
        error = f"HTTP error: {e.response.status_code}"
    except Exception as e:
        error = f"Unexpected error: {e}"

    return templates.TemplateResponse("index.html", {"request": request, "result": result, "error": error})

# Health check
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}