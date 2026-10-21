"""AliceClient 的行为测试。

全部走假传输层（RecordingTransport），不打真网、不碰真凭据。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from checkin_windalice.client import (
    AliceClient,
    ApiError,
    AuthError,
    Credentials,
    Response,
)

# 真实抓到的响应（今天已领取的状态），原样用作夹具
STATUS_ALREADY_CLAIMED: dict[str, Any] = {
    "code": 0,
    "message": "操作成功",
    "data": {"claimable": False, "unclaimableReason": "今日已领取", "rewardPoints": 200},
    "success": True,
}

STATUS_CLAIMABLE: dict[str, Any] = {
    "code": 0,
    "message": "操作成功",
    "data": {"claimable": True, "unclaimableReason": None, "rewardPoints": 200},
    "success": True,
}

CREDS = Credentials(
    refresh_token="fake-refresh-token-value",
    session_id="fake-session-id",
    header_session_id="fake-header-session-id",
)


class RecordingTransport:
    """记录每次调用的 (method, url, headers, body)，按顺序返回预置响应。

    预置项是 Exception 时直接抛出，用来模拟网络层故障。
    """

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


def ok(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(status=status, body=json.dumps(payload).encode("utf-8"))


def make_client(transport: RecordingTransport) -> AliceClient:
    return AliceClient(
        base_url="https://alice.wind.com.cn",
        credentials=CREDS,
        transport=transport,
    )


def test_status_parses_already_claimed() -> None:
    transport = RecordingTransport(ok(STATUS_ALREADY_CLAIMED))

    status = make_client(transport).check_in_status()

    assert status.claimable is False
    assert status.reason == "今日已领取"
    assert status.reward_points == 200


def test_status_parses_claimable() -> None:
    transport = RecordingTransport(ok(STATUS_CLAIMABLE))

    status = make_client(transport).check_in_status()

    assert status.claimable is True


def test_status_hits_documented_endpoint() -> None:
    transport = RecordingTransport(ok(STATUS_ALREADY_CLAIMED))

    make_client(transport).check_in_status()

    assert transport.calls[0]["method"] == "GET"
    assert (
        transport.calls[0]["url"]
        == "https://alice.wind.com.cn/aggregation/api/v1/points/daily-check-in/status"
    )


def test_claim_posts_to_claim_endpoint() -> None:
    transport = RecordingTransport(ok({"code": 0, "data": {"rewardPoints": 200}, "success": True}))

    make_client(transport).claim()

    assert transport.calls[0]["method"] == "POST"
    assert (
        transport.calls[0]["url"]
        == "https://alice.wind.com.cn/aggregation/api/v1/points/daily-check-in/claim"
    )


def test_claim_reads_awarded_points() -> None:
    transport = RecordingTransport(ok({"code": 0, "data": {"rewardPoints": 200}, "success": True}))

    result = make_client(transport).claim()

    assert result.points == 200


def test_claim_tolerates_missing_points_field() -> None:
    """服务端没回积分字段时不应崩，交由调用方用 status 里的期望值兜底。"""
    transport = RecordingTransport(ok({"code": 0, "data": {}, "success": True}))

    result = make_client(transport).claim()

    assert result.points is None


def test_every_request_carries_auth_cookie_and_session_header() -> None:
    transport = RecordingTransport(ok(STATUS_ALREADY_CLAIMED))

    make_client(transport).check_in_status()

    headers = transport.calls[0]["headers"]
    cookie = headers["Cookie"]
    assert "weaver-auth.refresh-token=fake-refresh-token-value" in cookie
    assert "weaver-auth.session-id=fake-session-id" in cookie
    assert headers["wind.sessionid"] == "fake-header-session-id"


def test_401_raises_auth_error() -> None:
    transport = RecordingTransport(
        Response(status=401, body=json.dumps({"error": "unauthorized"}).encode())
    )

    with pytest.raises(AuthError):
        make_client(transport).check_in_status()


def test_auth_error_message_never_leaks_credentials() -> None:
    transport = RecordingTransport(Response(status=401, body=b"{}"))

    with pytest.raises(AuthError) as excinfo:
        make_client(transport).check_in_status()

    message = str(excinfo.value)
    assert "fake-refresh-token-value" not in message
    assert "fake-session-id" not in message


def test_auth_error_surfaces_upstream_reason_for_diagnosis() -> None:
    """服务端会给出 kind/reason（例如 refresh_token_rejected_cached），排障全靠它。"""
    transport = RecordingTransport(
        Response(
            status=401,
            body=json.dumps(
                {
                    "error": "Authentication required",
                    "kind": "credentials_rejected",
                    "reason": "refresh_token_rejected_cached",
                }
            ).encode(),
        )
    )

    with pytest.raises(AuthError) as excinfo:
        make_client(transport).check_in_status()

    assert "refresh_token_rejected_cached" in str(excinfo.value)


def test_server_error_raises_api_error() -> None:
    transport = RecordingTransport(Response(status=500, body=b"boom"))

    with pytest.raises(ApiError):
        make_client(transport).check_in_status()


def test_network_error_raises_api_error() -> None:
    transport = RecordingTransport(OSError("connection refused"))

    with pytest.raises(ApiError) as excinfo:
        make_client(transport).check_in_status()

    assert "网络请求失败" in str(excinfo.value)


def test_success_false_raises_api_error() -> None:
    """接口用 success=false + code!=0 表示业务失败（例如 records 的 code 3）。"""
    transport = RecordingTransport(
        ok({"code": 3, "message": "系统异常", "data": None, "success": False})
    )

    with pytest.raises(ApiError) as excinfo:
        make_client(transport).check_in_status()

    assert "系统异常" in str(excinfo.value)


def test_non_json_body_raises_api_error() -> None:
    transport = RecordingTransport(Response(status=200, body=b"<html>not json</html>"))

    with pytest.raises(ApiError):
        make_client(transport).check_in_status()
