"""Wind Alice 签到接口的最小 HTTP 客户端。

设计要点：
- 只依赖标准库（urllib），不引入运行时第三方依赖。
- 认证是三个值的组合：两个 httpOnly cookie + 一个自定义请求头。
- 传输层可注入（``transport``），测试完全不需要联网。
- 任何异常消息都不得包含凭据，避免泄漏进 GitHub Actions 日志。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._http import DEFAULT_TIMEOUT_SECONDS, Response, Transport, preview, urllib_transport

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "AliceClient",
    "AliceError",
    "ApiError",
    "AuthError",
    "CheckInStatus",
    "ClaimResult",
    "Credentials",
    "Response",
    "Transport",
]

DEFAULT_BASE_URL = "https://alice.wind.com.cn"
STATUS_PATH = "/aggregation/api/v1/points/daily-check-in/status"
CLAIM_PATH = "/aggregation/api/v1/points/daily-check-in/claim"


class AliceError(Exception):
    """本模块所有异常的基类。"""


class AuthError(AliceError):
    """凭据无效/过期（HTTP 401）。"""


class ApiError(AliceError):
    """网络、协议或业务层失败。"""


@dataclass(frozen=True)
class Credentials:
    """Wind Alice 的登录凭据三件套。"""

    refresh_token: str
    session_id: str
    header_session_id: str


@dataclass(frozen=True)
class CheckInStatus:
    """今日签到状态。"""

    claimable: bool
    reason: str
    reward_points: int


@dataclass(frozen=True)
class ClaimResult:
    """领取结果。``points`` 为 None 表示服务端没返回数值。"""

    points: int | None


class AliceClient:
    """只暴露两个业务动作：查状态、领取。"""

    def __init__(
        self,
        credentials: Credentials,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
    ) -> None:
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._transport: Transport = transport or urllib_transport

    def check_in_status(self) -> CheckInStatus:
        data = self._request("GET", STATUS_PATH)
        return CheckInStatus(
            claimable=bool(data.get("claimable")),
            reason=str(data.get("unclaimableReason") or ""),
            reward_points=_as_int(data.get("rewardPoints")),
        )

    def claim(self) -> ClaimResult:
        data = self._request("POST", CLAIM_PATH)
        raw = data.get("rewardPoints", data.get("points"))
        return ClaimResult(points=None if raw is None else _as_int(raw))

    def _request(self, method: str, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = b"" if method == "POST" else None
        try:
            response: Response = self._transport(method, url, self._headers(), body)
        except OSError as exc:  # URLError / 超时 / DNS 失败
            raise ApiError(f"网络请求失败：{exc}") from exc

        if response.status == 401:
            raise AuthError(
                "凭据已失效（HTTP 401）：refresh-token 过期或被上游拒绝，"
                "请重新抓取后更新 GitHub Secrets" + _upstream_hint(response.body)
            )
        if response.status >= 400:
            raise ApiError(f"HTTP {response.status}：{preview(response.body)}")

        payload = _parse_json(response.body)
        success = payload.get("success")
        code = payload.get("code", 0)
        if success is False or (isinstance(code, int) and code != 0):
            raise ApiError(f"接口返回失败：code={code} message={payload.get('message') or '(无)'}")

        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _headers(self) -> dict[str, str]:
        cookie = "; ".join(
            [
                f"weaver-auth.refresh-token={self._credentials.refresh_token}",
                f"weaver-auth.session-id={self._credentials.session_id}",
            ]
        )
        return {
            "Accept": "application/json",
            "Cookie": cookie,
            "wind.sessionid": self._credentials.header_session_id,
        }


def _parse_json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(f"响应不是合法 JSON：{preview(body)}") from exc
    if not isinstance(payload, dict):
        raise ApiError(f"响应不是 JSON 对象：{preview(body)}")
    return payload


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _upstream_hint(body: bytes) -> str:
    """从网关的错误体里摘出 kind/reason 便于排障（这些字段不含凭据）。"""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    parts = [str(payload[key]) for key in ("kind", "reason") if payload.get(key)]
    return f"（上游返回：{' / '.join(parts)}）" if parts else ""
