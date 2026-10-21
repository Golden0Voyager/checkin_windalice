"""Bark 推送的行为测试。

关键约束：通知永远不能把签到流程搞挂，也永远不能在报错里泄漏 key。
"""

from __future__ import annotations

import json
from typing import Any

from checkin_windalice._http import Response
from checkin_windalice.notify import BarkNotifier, NoopNotifier

FAKE_KEY = "fake-bark-key"
FAKE_BASE = "https://api.day.app"


class RecordingTransport:
    def __init__(self, *responses: Response | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> Response:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if not self._responses:
            raise AssertionError("transport 收到的调用次数多于预置响应")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(payload: dict[str, Any]) -> Response:
    return Response(status=200, body=json.dumps(payload).encode("utf-8"))


def test_send_posts_json_to_bark_endpoint() -> None:
    transport = RecordingTransport(ok({"code": 200, "message": "success"}))

    BarkNotifier(key=FAKE_KEY, transport=transport).send("标题", "内容")

    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == f"{FAKE_BASE}/{FAKE_KEY}"
    payload = json.loads(transport.calls[0]["body"])
    assert payload["title"] == "标题"
    assert payload["body"] == "内容"
    assert payload["group"] == "windalice"


def test_send_returns_true_on_success() -> None:
    transport = RecordingTransport(ok({"code": 200}))

    assert BarkNotifier(key=FAKE_KEY, transport=transport).send("t", "b") is True


def test_send_returns_false_on_http_error() -> None:
    transport = RecordingTransport(Response(status=500, body=b"boom"))

    assert BarkNotifier(key=FAKE_KEY, transport=transport).send("t", "b") is False


def test_send_returns_false_on_network_error_instead_of_raising() -> None:
    transport = RecordingTransport(OSError("connection refused"))

    assert BarkNotifier(key=FAKE_KEY, transport=transport).send("t", "b") is False


def test_send_returns_false_on_non_json_body_instead_of_raising() -> None:
    transport = RecordingTransport(Response(status=200, body=b"<html>"))

    assert BarkNotifier(key=FAKE_KEY, transport=transport).send("t", "b") is False


def test_send_returns_false_when_server_reports_failure_code() -> None:
    transport = RecordingTransport(ok({"code": 400, "message": "invalid key"}))

    assert BarkNotifier(key=FAKE_KEY, transport=transport).send("t", "b") is False


def test_custom_base_url_is_honoured() -> None:
    transport = RecordingTransport(ok({"code": 200}))

    BarkNotifier(key=FAKE_KEY, base_url="https://bark.example.com/", transport=transport).send(
        "t", "b"
    )

    assert transport.calls[0]["url"] == f"https://bark.example.com/{FAKE_KEY}"


def test_noop_notifier_never_calls_transport() -> None:
    assert NoopNotifier().send("t", "b") is False
