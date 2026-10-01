"""Claude-powered news read and trade decision.

Two calls: (1) Claude searches the live web for the latest drivers of the commodity and writes a
brief; (2) Claude combines that brief with the price data and returns a strict JSON decision.
Any failure (API error, refusal, unparseable output) becomes NO_TRADE.

Written to work on both the 0.x SDK (what pip installs on Python 3.9) and 1.x: newer request
fields are passed through `extra_body` / `extra_headers`.
"""
import json
import os

import anthropic

FALLBACK_BETA = "server-side-fallback-2026-07-01"

NEWS_SYSTEM = (
    "You are a commodities market analyst. Search the web for the most recent news (last 48 hours) "
    "that moves the price of the given commodity: supply disruptions, OPEC+ decisions, inventories, "
    "sanctions, geopolitics, demand data, central-bank or dollar moves, and scheduled events in the "
    "next 24 hours. Write a concise brief: bullet points with dates, each marked BULLISH, BEARISH or "
    "NEUTRAL for price, then a one-line net assessment. Report only what sources say; flag conflicts."
)

DECISION_SYSTEM = (
    "You are a disciplined intraday commodity options trader. Decide whether to buy a call (UP), "
    "buy a put (DOWN), or stay out (NO_TRADE) for the rest of today's session, which is closed "
    "before the market shuts tonight. Use both the price data and the news brief. Be calibrated: "
    "confidence is your honest probability (50-100) that price moves in your direction before the "
    "session ends. Prefer NO_TRADE when signals conflict or a major scheduled event is due within "
    "the session. All levels are prices of the TRADING futures contract given. entry_trigger is the "
    "futures level that must be crossed to confirm the move (below current price for DOWN, above "
    "for UP); stop_level invalidates the idea; target_level is a realistic take-profit for tonight. "
    "Set event_risk true if a scheduled release or announcement could whipsaw price tonight. "
    "watch_above / watch_below: trading-futures levels whose break later tonight would be worth "
    "a second look (a breakout above, a breakdown below); 0 when there is no such level. The bot "
    "only watches them when it does not trade on this decision."
)

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["UP", "DOWN", "NO_TRADE"]},
        "confidence": {"type": "integer"},
        "entry_trigger": {"type": "number"},
        "stop_level": {"type": "number"},
        "target_level": {"type": "number"},
        "event_risk": {"type": "boolean"},
        "watch_above": {"type": "number"},
        "watch_below": {"type": "number"},
        "reasoning": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["direction", "confidence", "entry_trigger", "stop_level", "target_level",
                 "event_risk", "watch_above", "watch_below", "reasoning", "key_risks"],
    "additionalProperties": False,
}

NO_TRADE = {"direction": "NO_TRADE", "confidence": 0, "entry_trigger": 0, "stop_level": 0,
            "target_level": 0, "event_risk": True, "watch_above": 0, "watch_below": 0, "key_risks": []}


def _why(e):
    """Short, readable reason for an API failure (status + Anthropic's own message)."""
    status = getattr(e, "status_code", "")
    msg = getattr(e, "message", "") or str(e)
    return f"{e.__class__.__name__} {status}: {msg}"[:300]


def _text(response):
    return "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def _sources(response):
    urls = []
    for b in response.content:
        if getattr(b, "type", "") == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
            urls += [getattr(r, "url", "") for r in b.content if getattr(r, "url", "")]
    return urls


class Analyst:
    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()

    def _create(self, **kwargs):
        extra_body = kwargs.pop("extra_body", {})
        extra_body.setdefault("output_config", {})["effort"] = self.cfg.effort
        extra_body["fallbacks"] = "default"
        headers = {"anthropic-beta": FALLBACK_BETA}
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
        if workspace:  # needed when the API key is user-level rather than workspace-scoped
            headers["anthropic-workspace-id"] = workspace
        return self.client.messages.create(
            model=self.cfg.model, extra_body=extra_body, extra_headers=headers, **kwargs)

    def news_brief(self, commodity):
        """Returns (brief_text, source_urls). Empty brief on failure."""
        messages = [{"role": "user", "content": f"Latest news moving {commodity} prices right now "
                                                f"(international benchmarks Brent/WTI and Indian MCX/NSE)."}]
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
        try:
            for _ in range(4):  # continue if the server pauses a long search turn
                r = self._create(max_tokens=16000, system=NEWS_SYSTEM, messages=messages, tools=tools)
                if r.stop_reason == "refusal":
                    return "", []
                if r.stop_reason != "pause_turn":
                    return _text(r), _sources(r)
                messages.append({"role": "assistant", "content": r.content})
        except anthropic.APIError as e:
            return f"(news unavailable: {_why(e)})", []
        return "", []

    def decide(self, commodity, tech, trade_future, trade_future_price, news, now_text, update=""):
        """Returns (decision_dict, raw_text). NO_TRADE on any problem.

        `update` describes what changed since an earlier decision tonight (used for the re-check)."""
        prompt = (
            f"Time now: {now_text} IST. Commodity: {commodity}.\n"
            f"TRADING futures contract: {trade_future} at {trade_future_price}.\n"
            f"Price data (from the most liquid exchange, may differ slightly in level):\n"
            f"{json.dumps(tech, indent=1)}\n\nNews brief:\n{news or '(no news available)'}\n\n"
            + (f"Update since your earlier decision tonight:\n{update}\n\n" if update else "")
            + "Return your decision.")
        try:
            r = self._create(
                max_tokens=16000, system=DECISION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                extra_body={"output_config": {"format": {"type": "json_schema", "schema": DECISION_SCHEMA}}})
        except anthropic.APIError as e:
            return dict(NO_TRADE, reasoning=f"AI call failed: {_why(e)}"), ""
        raw = _text(r)
        if r.stop_reason in ("refusal", "max_tokens"):
            return dict(NO_TRADE, reasoning=f"AI stopped: {r.stop_reason}"), raw
        try:
            d = json.loads(raw)
            d.setdefault("watch_above", 0)  # optional extras: no levels means nothing to watch
            d.setdefault("watch_below", 0)
            if set(DECISION_SCHEMA["required"]) - set(d):
                raise ValueError("missing fields")
            return d, raw
        except ValueError:
            return dict(NO_TRADE, reasoning="AI output was not valid JSON"), raw
