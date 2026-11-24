"""Agent tools: get_weather, get_news, filter_by_interest.

Each tool returns a plain dict. On failure a tool returns
{"unavailable": True, "error": "..."} instead of raising, so the agent can
skip the source and still produce a briefing.
"""
import json
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 8

WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
    81: "heavy rain showers", 82: "violent rain showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}

NEWS_URL = "https://news.google.com/rss/search"
MAX_TOPICS = 10
ITEMS_PER_TOPIC = 3
NEWS_ATTEMPTS = 2
NEWS_RETRY_DELAY = 0.7
MAX_RESPONSE_BYTES = 1_000_000


def _http_json(url, params):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "daily-briefing/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_text(url, params):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "daily-briefing/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return raw.decode("utf-8", errors="replace")


def _published_ts(item):
    try:
        return parsedate_to_datetime(item["published"]).timestamp()
    except Exception:
        return 0.0


def _parse_rss(xml_text, topic, limit):
    """Parse RSS items. Rejects DTD/entity declarations (XML bomb protection)."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("unexpected DTD in feed")
    root = ElementTree.fromstring(xml_text)
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        source = (node.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        items.append({
            "title": title,
            "source": source,
            "published": (node.findtext("pubDate") or "").strip(),
            "tags": [topic.lower()],
        })
        if len(items) >= limit:
            break
    return items


def _geocode(city):
    """Return {name, country, country_code, latitude, longitude} or None.

    Open-Meteo first. Its place list misses some spellings (e.g. Tadipatri is
    stored as Tadpatri), so fall back to OpenStreetMap Nominatim.
    """
    try:
        geo = _http_json(GEOCODE_URL, {"name": city, "count": 1, "format": "json"})
        if geo.get("results"):
            r = geo["results"][0]
            return {"name": r.get("name", city), "country": r.get("country", ""),
                    "country_code": (r.get("country_code") or "").upper(),
                    "latitude": r["latitude"], "longitude": r["longitude"]}
    except Exception:
        pass  # fall through to the second geocoder
    found = _http_json(NOMINATIM_URL, {"q": city, "format": "json", "limit": 1, "addressdetails": 1})
    if found:
        r = found[0]
        addr = r.get("address", {})
        return {"name": city.title(), "country": addr.get("country", ""),
                "country_code": (addr.get("country_code") or "").upper(),
                "latitude": float(r["lat"]), "longitude": float(r["lon"])}
    return None


def get_weather(city, fail=False, ctx=None):
    """Current weather and a 3-day outlook for a city (Open-Meteo)."""
    if fail:
        return {"unavailable": True, "error": "weather source simulated failure"}
    try:
        place = _geocode(city)
        if not place:
            return {"unavailable": True, "error": f"city not found: {city}"}
        if ctx is not None:
            ctx["country_code"] = place["country_code"]
        data = _http_json(FORECAST_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 3,
            "timezone": "auto",
        })
        cur = data["current"]
        daily = data["daily"]
        days = []
        for i, day in enumerate(daily["time"]):
            days.append({
                "date": day,
                "summary": WEATHER_CODES.get(daily["weather_code"][i], "unknown"),
                "high_c": daily["temperature_2m_max"][i],
                "low_c": daily["temperature_2m_min"][i],
                "rain_chance_pct": daily["precipitation_probability_max"][i],
            })
        return {
            "city": place["name"],
            "country": place["country"],
            "current": {
                "temperature_c": cur["temperature_2m"],
                "summary": WEATHER_CODES.get(cur["weather_code"], "unknown"),
                "wind_kmh": cur["wind_speed_10m"],
            },
            "forecast": days,
        }
    except Exception as exc:  # network, parse, or API errors: degrade, don't raise
        return {"unavailable": True, "error": f"weather lookup failed: {type(exc).__name__}"}


def _fetch_topic(topic, cc_edition):
    """Headlines for one topic. Tries the regional edition, then US; if both
    fail or are empty, waits briefly and tries again (feeds are occasionally
    empty for a moment). Returns (items, had_error)."""
    had_error = False
    for attempt in range(NEWS_ATTEMPTS):
        if attempt:
            time.sleep(NEWS_RETRY_DELAY)
        for cc in dict.fromkeys([cc_edition, "US"]):
            try:
                xml_text = _http_text(NEWS_URL, {"q": f"{topic} when:1d", "hl": f"en-{cc}", "gl": cc, "ceid": f"{cc}:en"})
                found = _parse_rss(xml_text, topic, ITEMS_PER_TOPIC)
            except Exception:
                had_error = True
                continue
            if found:
                return found, had_error
    return [], had_error


def get_news(topics, fail=False, country_code=""):
    """Recent headlines for each topic (Google News RSS, no API key), newest first.

    country_code picks the regional Google News edition (default US).
    """
    if fail:
        return {"unavailable": True, "error": "news source simulated failure"}
    topics = [str(t).strip() for t in topics if str(t).strip()][:MAX_TOPICS]
    if not topics:
        return {"unavailable": True, "error": "no topics given"}
    cc_edition = country_code.upper() if len(country_code) == 2 and country_code.isalpha() else "US"
    items, seen, errors = [], set(), 0
    for topic in topics:
        found, had_error = _fetch_topic(topic, cc_edition)  # one bad topic must not sink the rest
        errors += had_error and not found
        for item in found:
            if item["title"] not in seen:
                seen.add(item["title"])
                items.append(item)
    if not items:
        return {"unavailable": True, "error": "news lookup failed" if errors else "no recent news found"}
    items.sort(key=_published_ts, reverse=True)
    return {"items": items}


def filter_by_interest(items, interests):
    """Keep only items whose title, summary or tags match an interest."""
    keys = [i.lower().strip() for i in interests if i and i.strip()]
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        haystack = " ".join([
            str(item.get("title", "")),
            str(item.get("summary", "")),
            " ".join(map(str, item.get("tags", []))),
        ]).lower()
        if any(k in haystack for k in keys):
            kept.append(item)
    return {"items": kept, "kept": len(kept), "dropped": len(items) - len(kept)}


TOOL_SPECS = [
    {"toolSpec": {
        "name": "get_weather",
        "description": "Get current weather and a 3-day forecast for a city. Call this once.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name, e.g. 'Hyderabad'"}},
            "required": ["city"],
        }},
    }},
    {"toolSpec": {
        "name": "get_news",
        "description": "Fetch recent news headlines for each topic. Then pass the items to filter_by_interest.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"topics": {"type": "array", "items": {"type": "string"}, "description": "Topics the user cares about"}},
            "required": ["topics"],
        }},
    }},
    {"toolSpec": {
        "name": "filter_by_interest",
        "description": "Keep only news items that match the user's interests. Pass the items returned by get_news.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object"}, "description": "News items from get_news"},
                "interests": {"type": "array", "items": {"type": "string"}, "description": "User interests"},
            },
            "required": ["items", "interests"],
        }},
    }},
]


def run_tool(name, args, fail_sources=(), ctx=None):
    """Dispatch a tool call. Unknown tools and bad args return an error dict."""
    try:
        if name == "get_weather":
            return get_weather(str(args["city"]), fail="weather" in fail_sources, ctx=ctx)
        if name == "get_news":
            return get_news(list(args["topics"]), fail="news" in fail_sources,
                            country_code=(ctx or {}).get("country_code", ""))
        if name == "filter_by_interest":
            return filter_by_interest(list(args["items"]), list(args["interests"]))
        return {"error": f"unknown tool: {name}"}
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"bad arguments for {name}: {type(exc).__name__}"}
