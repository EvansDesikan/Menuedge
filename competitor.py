"""
MenuEdge — Competitor Intelligence Engine
========================================
1. Google Maps Places API  → find nearby restaurants (same cuisine, 10km)
2. Place Details API       → get website URL, menu URL, rating, price level
3. Web scraper             → fast path (requests+BS4), Playwright fallback for JS sites
4. Claude                  → compare menus, identify gaps, build recommendations
"""

import os, re, json, time
import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic

# Playwright is used as a fallback for JS-heavy menu pages
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False

GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
client    = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

PLUS_CODE_RE = re.compile(r"^[23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,3}", re.IGNORECASE)

# ── Step 1: Geocode location ───────────────────────────────────────────────────
def geocode(location: str) -> tuple[float, float] | None:
    # Detect Google Plus Codes (e.g. "VJ87+85 Murfreesboro, TN")
    if PLUS_CODE_RE.match(location.strip()):
        raise ValueError(
            "That looks like a Google Plus Code (e.g. VJ87+85). "
            "Please enter a standard street address instead, "
            "e.g. '1650 Memorial Blvd, Murfreesboro, TN 37129'."
        )
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    r = requests.get(url, params={"address": location, "key": GMAPS_KEY}, timeout=10)
    data = r.json()
    status = data.get("status")
    print(f"  [geocode] status={status} error={data.get('error_message','')}")
    if status == "OK":
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    err = data.get("error_message") or status or "Unknown geocoding error"
    raise ValueError(f"Geocoding failed: {err}")


# ── Step 2: Find nearby restaurants ───────────────────────────────────────────
def find_competitors(lat: float, lng: float, cuisine: str, radius_m=10000, max_results=8) -> list[dict]:
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "type": "restaurant",
        "keyword": cuisine,
        "key": GMAPS_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    places = []
    for p in data.get("results", [])[:max_results]:
        places.append({
            "place_id":    p.get("place_id"),
            "name":        p.get("name"),
            "rating":      p.get("rating"),
            "user_ratings": p.get("user_ratings_total", 0),
            "price_level": p.get("price_level"),   # 1-4 ($, $$, $$$, $$$$)
            "vicinity":    p.get("vicinity"),
            "types":       p.get("types", []),
        })
    return places


# ── Step 3: Get place details (website, phone) ────────────────────────────────
def get_place_details(place_id: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,website,formatted_phone_number,opening_hours,price_level,rating,menu",
        "key": GMAPS_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    return data.get("result", {})


# ── Step 4: Scrape menu from website ─────────────────────────────────────────

MENU_PATHS = ["", "/menu", "/food", "/our-menu", "/food-menu",
              "/menu/", "/menus", "/order", "/online-menu"]

PRICE_RE = re.compile(r"(.{3,60}?)\s*[\$£€]\s*(\d+\.?\d{0,2})", re.MULTILINE)


def _parse_html(html: str) -> list[dict]:
    """Extract menu items from raw HTML using JSON-LD and price pattern strategies."""
    items = []
    soup = BeautifulSoup(html, "html.parser")

    # Strategy A: JSON-LD structured data (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            if isinstance(ld, list):
                ld = ld[0]
            menu_data = ld.get("hasMenu") or ld.get("menu") or {}
            if isinstance(menu_data, str):
                continue
            for section in menu_data.get("hasMenuSection", []):
                cat = section.get("name", "")
                for mi in section.get("hasMenuItem", []):
                    offer = mi.get("offers", {})
                    price = offer.get("price") if isinstance(offer, dict) else None
                    items.append({
                        "name": mi.get("name", ""),
                        "description": mi.get("description", ""),
                        "price": float(price) if price else None,
                        "category": cat,
                    })
        except Exception:
            pass

    if items:
        return items

    # Strategy B: price patterns in visible text
    text = soup.get_text(separator="\n")
    for match in PRICE_RE.finditer(text):
        name = match.group(1).strip().strip("•–-|").strip()
        price = float(match.group(2))
        if 3 <= len(name) <= 60 and 1 <= price <= 200:
            items.append({"name": name, "price": price,
                          "description": "", "category": "Menu"})
    return items


def _dedup(items: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for item in items:
        key = item["name"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:40]


def _fast_scrape(url: str) -> list[dict]:
    """Fast path: plain HTTP request + BeautifulSoup."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
        if r.status_code == 200:
            return _parse_html(r.text)
    except Exception:
        pass
    return []


def _playwright_scrape(url: str) -> list[dict]:
    """Fallback: full browser render via Playwright for JS-heavy pages."""
    if not _PLAYWRIGHT_OK:
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=20000)
            # Give JS menus extra time to render
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
        return _parse_html(html)
    except Exception:
        return []


def scrape_menu(website: str, gmaps_menu_url: str = "") -> list[dict]:
    """
    Try multiple strategies to extract menu items from a restaurant website.
    Priority order:
      1. Google Maps menu URL (direct link, often a dedicated menu page)
      2. Common sub-paths on the restaurant's own website
    For each URL: fast HTTP scrape first; if <5 items found, retry with Playwright.
    Returns list of {name, price, description, category} dicts.
    """
    if not website and not gmaps_menu_url:
        return []

    # Build ordered list of URLs to try — Google Maps menu link first
    urls_to_try = []
    if gmaps_menu_url:
        urls_to_try.append(gmaps_menu_url)
    if website:
        for path in MENU_PATHS:
            urls_to_try.append(website.rstrip("/") + path)

    for url in urls_to_try:
        items = _fast_scrape(url)
        if len(items) >= 5:
            return _dedup(items)

        # Fast scrape found nothing meaningful — try Playwright
        print(f"    [playwright] rendering {url}")
        items = _playwright_scrape(url)
        if len(items) >= 5:
            return _dedup(items)

    # Return whatever we found even if sparse
    all_items = []
    for url in urls_to_try[:3]:  # avoid infinite scraping
        all_items.extend(_fast_scrape(url))
    return _dedup(all_items)


# ── Step 5: Claude competitive analysis ───────────────────────────────────────
def analyse_competition(client_name: str, client_cuisine: str,
                         client_items: list[dict],
                         competitors: list[dict]) -> dict:
    """
    Use Claude to compare the client's menu against competitors and
    produce a structured competitive intelligence report.
    """

    # Build competitor summary
    comp_summary = []
    for c in competitors:
        entry = {
            "name":        c.get("name"),
            "rating":      c.get("rating"),
            "price_level": "$" * (c.get("price_level") or 2),
            "distance_km": c.get("distance_km"),
            "menu_items":  c.get("menu_items", [])[:20],  # limit for context
            "menu_scraped": len(c.get("menu_items", [])) > 0,
        }
        comp_summary.append(entry)

    prompt = f"""You are a restaurant business strategist specialising in competitive menu analysis.

CLIENT RESTAURANT: {client_name}
CUISINE TYPE: {client_cuisine}

CLIENT'S CURRENT MENU:
{json.dumps(client_items[:30], indent=2)}

NEARBY COMPETITORS (within 10km):
{json.dumps(comp_summary, indent=2)}

Produce a comprehensive competitive intelligence report as JSON:

{{
  "competitive_overview": {{
    "total_competitors_found": <number>,
    "competitors_with_menu_data": <number>,
    "market_summary": "<2-3 sentences about the competitive landscape>",
    "client_position": "<Premium|Mid-range|Budget|Unknown>",
    "market_avg_price_level": "<$ | $$ | $$$ | $$$$>"
  }},
  "price_analysis": {{
    "client_avg_price": <number or null>,
    "market_avg_price": <number or null>,
    "price_verdict": "<Overpriced|Competitive|Underpriced|Unknown>",
    "price_insight": "<1-2 sentences>",
    "recommended_price_adjustment": "<e.g. Raise prices by 8-12% or No change needed>"
  }},
  "menu_gaps": [
    {{
      "dish": "<dish name>",
      "why_add": "<why this dish would attract customers — 1 sentence>",
      "competitors_offering": ["<competitor name>"],
      "suggested_price": "<e.g. £12.99>",
      "priority": "<High|Medium|Low>"
    }}
  ],
  "unique_advantages": [
    {{
      "item_or_feature": "<what the client has that competitors don't>",
      "how_to_leverage": "<1 sentence on how to market this>"
    }}
  ],
  "competitor_insights": [
    {{
      "name": "<competitor name>",
      "rating": <number>,
      "price_level": "<$-$$$$>",
      "threat_level": "<High|Medium|Low>",
      "their_strength": "<what they do well>",
      "their_weakness": "<where client can beat them>",
      "key_dishes_to_beat": ["<dish>"]
    }}
  ],
  "quick_wins": [
    {{
      "action": "<specific action to take>",
      "impact": "<expected outcome>",
      "effort": "<Easy|Medium|Hard>"
    }}
  ],
  "strategic_recommendations": [
    "<recommendation 1>",
    "<recommendation 2>",
    "<recommendation 3>",
    "<recommendation 4>"
  ]
}}

Base your analysis on the actual data provided. If menu data is missing for competitors,
use their rating, price level, and cuisine type to infer insights.
Return ONLY the JSON, no other text."""

    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = re.sub(r"^```[a-z]*\n?", "", resp.content[0].text.strip()).rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"error": "Analysis failed", "raw": raw}


# ── Main entry point ──────────────────────────────────────────────────────────
def run_competitive_analysis(restaurant_name: str, location: str,
                              cuisine: str, client_items: list[dict],
                              progress_cb=None, lat: float = None, lng: float = None) -> dict:
    """
    Full pipeline. progress_cb(step, message) called at each stage.
    Returns the full competitive intelligence dict.
    """

    def log(step, msg):
        print(f"  [competitive] {msg}")
        if progress_cb:
            progress_cb(step, msg)

    if not GMAPS_KEY:
        return {"error": "GOOGLE_MAPS_API_KEY not set in .env"}

    # 1. Geocode (skip if coordinates already provided)
    if lat is not None and lng is not None:
        log(1, f"Using provided coordinates: {lat:.4f}, {lng:.4f}")
    else:
        log(1, f"Locating {location}...")
        try:
            coords = geocode(location)
        except ValueError as e:
            return {"error": str(e)}
        if not coords:
            return {"error": f"Could not find location: {location}. Try a full street address, e.g. '123 Main St, City, State'."}
        lat, lng = coords
    log(1, f"Found: {lat:.4f}, {lng:.4f}")

    # 2. Find competitors
    log(2, f"Searching for {cuisine} restaurants within 10km...")
    competitors = find_competitors(lat, lng, cuisine, radius_m=10000, max_results=8)
    log(2, f"Found {len(competitors)} competitors")

    if not competitors:
        log(2, f"No cuisine-specific results, broadening search...")
        competitors = find_competitors(lat, lng, cuisine, radius_m=25000, max_results=8)
    if not competitors:
        log(2, f"Trying any restaurants nearby...")
        competitors = find_competitors(lat, lng, "restaurant", radius_m=10000, max_results=8)
    if not competitors:
        return {"error": f"No restaurants found near your location. Try typing a specific city or address instead."}

    # 3. Get details + scrape menus
    for i, comp in enumerate(competitors):
        log(3, f"Analysing {comp['name']}...")
        details = get_place_details(comp["place_id"])
        comp["website"]      = details.get("website", "")
        comp["phone"]        = details.get("formatted_phone_number", "")
        comp["gmaps_menu"]   = details.get("menu", "")

        # Calculate distance
        try:
            dist_url = "https://maps.googleapis.com/maps/api/distancematrix/json"
            dr = requests.get(dist_url, params={
                "origins": f"{lat},{lng}",
                "destinations": comp["vicinity"],
                "key": GMAPS_KEY,
            }, timeout=8)
            dd = dr.json()
            dist_m = dd["rows"][0]["elements"][0]["distance"]["value"]
            comp["distance_km"] = round(dist_m / 1000, 1)
        except Exception:
            comp["distance_km"] = None

        # Scrape menu — prefer Google Maps menu URL, fall back to website
        if comp["website"] or comp["gmaps_menu"]:
            src = "Google Maps menu" if comp["gmaps_menu"] else "website"
            log(3, f"  Scanning menu for {comp['name']} (via {src})...")
            comp["menu_items"] = scrape_menu(comp["website"], comp["gmaps_menu"])
            log(3, f"  Found {len(comp['menu_items'])} items")
        else:
            comp["menu_items"] = []

        time.sleep(0.5)  # be polite to websites

    # 4. Claude analysis
    log(4, "Running competitive analysis with AI...")
    analysis = analyse_competition(restaurant_name, cuisine, client_items, competitors)

    return {
        "status": "ok",
        "location": {"lat": lat, "lng": lng, "query": location},
        "competitors": competitors,
        "analysis": analysis,
    }
