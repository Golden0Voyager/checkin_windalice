"""极简 HTTP 传输层：可注入、无第三方依赖。

把 HTTP 错误码也当作正常响应返回，由上层业务模块统一判读语义。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Response:
    """传输层返回的原始响应。"""

    status: int
    body: bytes


class Transport(Protocol):
    """可注入的传输层：测试用假实现，线上用 :func:`urllib_transport`。"""

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> Response: ...


def urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(status=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return Response(status=exc.code, body=exc.read())


def preview(body: bytes, limit: int = 200) -> str:
    """截断响应体用于报错，避免日志被大响应淹没。"""
    return body[:limit].decode("utf-8", errors="replace")
