import json

import pytest

import agent
import guardrails
import handler
import tools


SAMPLE_NEWS = [
    {"title": "New AI chip announced", "source": "TechDaily", "published": "", "tags": ["technology"]},
    {"title": "India win cricket series", "source": "Sports", "published": "", "tags": ["cricket"]},
    {"title": "Central bank holds rates", "source": "Finance", "published": "", "tags": ["finance"]},
]

EMPTY_FEED = "<rss><channel></channel></rss>"

SAMPLE_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Chip startup raises funds - TechDaily</title><source>TechDaily</source><pubDate>Sun, 22 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Second story - Wire</title><source>Wire</source></item>
<item><title>Third story - Wire</title><source>Wire</source></item>
<item><title>Fourth story - Wire</title><source>Wire</source></item>
</channel></rss>"""


class FakeBedrock:
    """Scripted converse(): calls the three tools in order, then writes text."""

    def __init__(self, final="Good morning!\nWeather: sunny.\nNews:\n- item", loop_forever=False):
        self.calls = 0
        self.final = final
        self.loop_forever = loop_forever
        self.tool_results = []

    def converse(self, **kw):
        self.calls += 1
        last = kw["messages"][-1]
        for block in last["content"]:
            if "toolResult" in block:
                self.tool_results.append(block["toolResult"]["content"][0]["json"])
        step = self.calls
        if self.loop_forever or step <= 3:
            name, args = [
                ("get_weather", {"city": "Paris"}),
                ("get_news", {"topics": ["technology"]}),
                ("filter_by_interest", {"items": SAMPLE_NEWS, "interests": ["technology"]}),
            ][(step - 1) % 3]
            msg = {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{step}", "name": name, "input": args}}]}
            return {"output": {"message": msg}, "stopReason": "tool_use"}
        msg = {"role": "assistant", "content": [{"text": self.final}]}
        return {"output": {"message": msg}, "stopReason": "end_turn"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    """Weather is offline by default; news returns a sample RSS feed."""
    monkeypatch.setattr(tools, "_http_json", lambda url, params: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(tools, "_http_text", lambda url, params: SAMPLE_RSS)


# ---- tools ----
def test_filter_keeps_matches_only():
    out = tools.filter_by_interest(SAMPLE_NEWS, ["cricket"])
    assert out["kept"] == 1 and "cricket" in out["items"][0]["title"].lower()
    assert out["dropped"] == len(SAMPLE_NEWS) - 1


def test_get_news_parses_rss_per_topic():
    out = tools.get_news(["technology", "cricket"])
    titles = [i["title"] for i in out["items"]]
    assert titles[0] == "Chip startup raises funds"  # " - Source" suffix removed
    assert out["items"][0]["source"] == "TechDaily" and out["items"][0]["tags"] == ["technology"]
    assert len(titles) == len(set(titles)) == 3  # max 3 per topic; same stories from 2 topics deduplicated


def test_get_news_all_topics_fail_is_unavailable(monkeypatch):
    monkeypatch.setattr(tools, "_http_text", lambda url, params: (_ for _ in ()).throw(OSError("down")))
    assert tools.get_news(["technology"])["unavailable"] is True


def test_get_news_partial_failure_keeps_other_topics(monkeypatch):
    def flaky(url, params):
        if "bad" in params["q"]:
            raise OSError("down")
        return SAMPLE_RSS
    monkeypatch.setattr(tools, "_http_text", flaky)
    assert tools.get_news(["bad", "technology"])["items"]


def test_get_news_rejects_xml_entities(monkeypatch):
    monkeypatch.setattr(tools, "_http_text", lambda url, params: '<!DOCTYPE x [<!ENTITY a "b">]><rss/>')
    assert tools.get_news(["technology"])["unavailable"] is True


def test_weather_failure_returns_unavailable_not_exception():
    assert tools.get_weather("Paris")["unavailable"] is True  # network blocked by fixture


def test_weather_parses_open_meteo(monkeypatch):
    def fake(url, params):
        if "geocoding" in url:
            return {"results": [{"name": "Paris", "country": "France", "latitude": 1, "longitude": 2}]}
        return {
            "current": {"temperature_2m": 18.5, "weather_code": 1, "wind_speed_10m": 9},
            "daily": {"time": ["d1", "d2", "d3"], "weather_code": [0, 61, 3],
                      "temperature_2m_max": [20, 19, 18], "temperature_2m_min": [10, 9, 8],
                      "precipitation_probability_max": [0, 60, 10]},
        }
    monkeypatch.setattr(tools, "_http_json", fake)
    out = tools.get_weather("Paris")
    assert out["current"]["summary"] == "mainly clear" and out["forecast"][1]["summary"] == "light rain"


def test_unknown_tool_and_bad_args():
    assert "error" in tools.run_tool("nope", {})
    assert "error" in tools.run_tool("get_weather", {})


# ---- guardrails ----
def test_validate_ok():
    r = guardrails.validate_request({"city": "São Paulo", "interests": "tech, cricket", "email": "A@B.com"})
    assert r["interests"] == ["tech", "cricket"] and r["email"] == "a@b.com"


@pytest.mark.parametrize("body", [
    {"city": "", "interests": "x"},
    {"city": "Paris; DROP TABLE", "interests": "x"},
    {"city": "Paris", "interests": ""},
    {"city": "Paris", "interests": ",".join(["a"] * 11)},
    {"city": "Paris", "interests": "<script>"},
    {"city": "Paris", "interests": "x", "email": "bad"},
    {"city": "Paris", "interests": "x", "simulate_failure": ["db"]},
    "not a dict",
])
def test_validate_rejects(body):
    with pytest.raises(guardrails.ValidationError):
        guardrails.validate_request(body)


def test_settings_requires_email():
    with pytest.raises(guardrails.ValidationError):
        guardrails.validate_request({"city": "Paris", "interests": "x"}, require_email=True)


def test_scrub_output():
    text = "key AKIAABCDEFGHIJKLMNOP and secret_key=abc123 ok"
    out = guardrails.scrub_output(text)
    assert "AKIA" not in out and "abc123" not in out and "ok" in out


# ---- agent ----
def test_agent_happy_path(monkeypatch):
    monkeypatch.setattr(tools, "get_weather", lambda city, fail=False: {"city": city, "current": {"summary": "clear"}})
    res = agent.run_agent(FakeBedrock(), "Paris", ["technology"])
    assert res["gaps"] == [] and res["iterations"] == 4
    assert [s["tool"] for s in res["sources"]] == ["get_weather", "get_news", "filter_by_interest"]
    assert res["items_kept"] > 0


def test_agent_dead_source_still_produces_briefing_with_note():
    res = agent.run_agent(FakeBedrock(), "Paris", ["technology"], fail_sources=("weather",))
    assert res["gaps"] == ["weather"]
    assert "weather unavailable today" in res["briefing"].lower()
    assert res["items_kept"] > 0  # news still worked


def test_agent_news_dead_still_produces_briefing(monkeypatch):
    monkeypatch.setattr(tools, "get_weather", lambda city, fail=False: {"city": city})
    res = agent.run_agent(FakeBedrock(), "Paris", ["technology"], fail_sources=("news",))
    assert res["gaps"] == ["news"] and "news unavailable today" in res["briefing"].lower()


def test_agent_both_sources_dead():
    res = agent.run_agent(FakeBedrock(), "Paris", ["x"], fail_sources=("weather", "news"))
    assert res["gaps"] == ["news", "weather"]
    assert "news unavailable today" in res["briefing"].lower()


def test_agent_max_iterations_enforced():
    fake = FakeBedrock(loop_forever=True)
    res = agent.run_agent(fake, "Paris", ["x"])
    assert fake.calls == agent.MAX_ITERATIONS
    assert "agent" in res["gaps"]


def test_agent_output_scrubbed():
    res = agent.run_agent(FakeBedrock(final="here AKIAABCDEFGHIJKLMNOP"), "Paris", ["x"])
    assert "AKIA" not in res["briefing"]


# ---- handler ----
def _event(method, path, body=None):
    return {"requestContext": {"http": {"method": method, "path": path}}, "body": json.dumps(body) if body is not None else None}


def test_handler_serves_page_and_404():
    assert handler.lambda_handler(_event("GET", "/"), None)["statusCode"] == 200
    assert handler.lambda_handler(_event("GET", "/x"), None)["statusCode"] == 404


def test_handler_preview_ok_and_400(monkeypatch):
    monkeypatch.setattr(handler, "bedrock", lambda: FakeBedrock())
    audits = []
    monkeypatch.setattr(handler.store, "write_audit", lambda *a: audits.append(a))
    ok = handler.lambda_handler(_event("POST", "/preview", {"city": "Paris", "interests": "technology"}), None)
    body = json.loads(ok["body"])
    assert ok["statusCode"] == 200 and "briefing" in body and len(audits) == 1
    assert [s["tool"] for s in body["sources"]] == ["get_weather", "get_news", "filter_by_interest"]
    assert "items_kept" in body and body["iterations"] == 4
    bad = handler.lambda_handler(_event("POST", "/preview", {"city": "", "interests": "x"}), None)
    assert bad["statusCode"] == 400


def test_handler_daily_continues_after_one_user_fails(monkeypatch):
    monkeypatch.setenv("SENDER_EMAIL", "from@x.com")
    users = [{"email": "a@x.com", "city": "Paris", "interests": ["technology"]},
             {"email": "b@x.com", "city": "Paris", "interests": ["technology"]}]
    monkeypatch.setattr(handler.store, "list_settings", lambda: users)
    monkeypatch.setattr(handler.store, "write_audit", lambda *a: None)
    monkeypatch.setattr(handler, "bedrock", lambda: FakeBedrock())
    sent_to = []

    class FakeSes:
        def send_email(self, **kw):
            to = kw["Destination"]["ToAddresses"][0]
            if to == "a@x.com":
                raise RuntimeError("not verified")
            sent_to.append(to)

    monkeypatch.setattr(handler, "ses", lambda: FakeSes())
    out = handler.lambda_handler({"source": "aws.events"}, None)
    assert out == {"sent": 1, "failed": 1} and sent_to == ["b@x.com"]


def test_handler_decodes_base64_body(monkeypatch):
    import base64
    monkeypatch.setattr(handler, "bedrock", lambda: FakeBedrock())
    monkeypatch.setattr(handler.store, "write_audit", lambda *a: None)
    ev = _event("POST", "/preview")
    ev["body"] = base64.b64encode(json.dumps({"city": "Paris", "interests": "technology"}).encode()).decode()
    ev["isBase64Encoded"] = True
    assert handler.lambda_handler(ev, None)["statusCode"] == 200


def test_clean_strips_chatter_and_rules():
    raw = "The news service is unavailable, so I won't retry it. Here is your morning briefing!\n\n---\n\nGood morning!\n\n---\nWeather: ok"
    assert agent._clean(raw) == "Good morning!\n\nWeather: ok"
    assert agent._clean("Good morning!\nNews:\n- a - b") == "Good morning!\nNews:\n- a - b"


def _forecast():
    return {
        "current": {"temperature_2m": 30, "weather_code": 0, "wind_speed_10m": 5},
        "daily": {"time": ["d1", "d2", "d3"], "weather_code": [0, 0, 0],
                  "temperature_2m_max": [33, 33, 33], "temperature_2m_min": [20, 20, 20],
                  "precipitation_probability_max": [0, 0, 0]},
    }


def test_weather_falls_back_to_nominatim_and_sets_country(monkeypatch):
    def fake(url, params):
        if "geocoding" in url:
            return {}  # Open-Meteo does not know the spelling
        if "nominatim" in url:
            return [{"lat": "14.9", "lon": "78.0", "address": {"country": "India", "country_code": "in"}}]
        return _forecast()
    monkeypatch.setattr(tools, "_http_json", fake)
    ctx = {}
    out = tools.get_weather("tadipatri", ctx=ctx)
    assert "unavailable" not in out and out["country"] == "India" and ctx["country_code"] == "IN"


def test_weather_city_unknown_to_both_geocoders(monkeypatch):
    monkeypatch.setattr(tools, "_http_json", lambda url, params: {} if "geocoding" in url else [])
    assert tools.get_weather("Xyzzyland")["error"].startswith("city not found")


def test_news_uses_country_edition_then_falls_back_to_us(monkeypatch):
    seen = []

    def fake(url, params):
        seen.append(params["gl"])
        return "<rss><channel></channel></rss>" if params["gl"] == "IN" else SAMPLE_RSS
    monkeypatch.setattr(tools, "_http_text", fake)
    out = tools.get_news(["technology"], country_code="in")
    assert seen == ["IN", "US"] and out["items"]


def test_news_sorted_newest_first(monkeypatch):
    rss = """<rss><channel>
    <item><title>Old - S</title><source>S</source><pubDate>Sun, 22 Nov 2026 01:00:00 GMT</pubDate></item>
    <item><title>New - S</title><source>S</source><pubDate>Sun, 22 Nov 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>"""
    monkeypatch.setattr(tools, "_http_text", lambda url, params: rss)
    assert [i["title"] for i in tools.get_news(["x"])["items"]] == ["New", "Old"]


def test_run_tool_passes_weather_country_to_news(monkeypatch):
    monkeypatch.setattr(tools, "get_weather", lambda city, fail=False, ctx=None: ctx.update(country_code="IN") or {"ok": 1})
    got = {}
    monkeypatch.setattr(tools, "get_news", lambda topics, fail=False, country_code="": got.setdefault("cc", country_code) and {})
    ctx = {}
    tools.run_tool("get_weather", {"city": "x"}, ctx=ctx)
    tools.run_tool("get_news", {"topics": ["a"]}, ctx=ctx)
    assert got["cc"] == "IN"



def test_to_html_lists_and_escaping():
    out = handler.to_html("Good morning!\n\nWeather: sunny <b>\n\nNews:\n\nStocks:\n- one & two\n\n- three\n\nTechnology:\n- four")
    assert out.count("<ul") == 2 and out.count("<li") == 3  # blank line inside a list does not split it
    assert "&amp;" in out and "&lt;b&gt;" in out and "<b>Stocks:</b>" in out and "<b>Weather:</b>" in out


def test_news_retries_after_empty_result(monkeypatch):
    calls = []

    def flaky(url, params):
        calls.append(params["gl"])
        return EMPTY_FEED if len(calls) <= 2 else SAMPLE_RSS  # first attempt (IN, US) empty
    monkeypatch.setattr(tools, "_http_text", flaky)
    out = tools.get_news(["stocks"], country_code="IN")
    assert out["items"] and calls == ["IN", "US", "IN"]


def test_news_retries_after_error_then_succeeds(monkeypatch):
    calls = []

    def flaky(url, params):
        calls.append(1)
        if len(calls) <= 2:
            raise OSError("blip")
        return SAMPLE_RSS
    monkeypatch.setattr(tools, "_http_text", flaky)
    assert tools.get_news(["stocks"], country_code="IN")["items"]  # IN and US fail, retry succeeds


def test_news_gives_up_after_two_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "_http_text", lambda url, params: calls.append(1) or EMPTY_FEED)
    out = tools.get_news(["stocks"], country_code="IN")
    assert out["unavailable"] is True and len(calls) == 4  # 2 attempts x 2 editions


def test_news_us_edition_used_when_regional_request_errors(monkeypatch):
    def fake(url, params):
        if params["gl"] == "IN":
            raise OSError("down")
        return SAMPLE_RSS
    monkeypatch.setattr(tools, "_http_text", fake)
    assert tools.get_news(["stocks"], country_code="IN")["items"]


def test_config_js_is_empty_javascript():
    out = handler.lambda_handler(_event("GET", "/config.js"), None)
    assert out["statusCode"] == 200 and out["body"] == ""
    assert out["headers"]["Content-Type"] == "application/javascript"
