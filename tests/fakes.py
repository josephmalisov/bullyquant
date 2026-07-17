"""Test doubles for the Anthropic client and QuantConnect HTTP layer."""

from __future__ import annotations

from types import SimpleNamespace


# ── Fake Anthropic client ─────────────────────────────────────────────────────

class _Block:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class _Resp:
    def __init__(self, text, input_tokens=100, output_tokens=50, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        return _Resp(self.parent.next_text())


class _BetaMessages(_Messages):
    pass


class FakeAnthropic:
    """Records call kwargs and returns queued/echoed text."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])
        self.messages = _Messages(self)
        self.beta = SimpleNamespace(messages = _BetaMessages(self))

    def next_text(self):
        if self._responses:
            return self._responses.pop(0)
        return "OK"


# ── Fake QuantConnect HTTP ────────────────────────────────────────────────────

class _HTTPResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeHTTP:
    """Maps (METHOD, endpoint-substring) -> payload dict (or a list to pop in order)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests = []

    def _match(self, method, url):
        for (m, frag), payload in self.routes.items():
            if m == method and frag in url:
                if isinstance(payload, list):
                    return payload.pop(0) if len(payload) > 1 else payload[0]
                return payload
        raise AssertionError(f"no fake route for {method} {url}")

    def post(self, url, json=None, auth=None, headers=None, timeout=None):
        self.requests.append(("POST", url, json))
        return _HTTPResp(self._match("POST", url))

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        self.requests.append(("GET", url, params))
        return _HTTPResp(self._match("GET", url))
