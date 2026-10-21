"""Bark 推送通知（https://bark.day.app）。

设计约束：
- 通知失败绝不影响签到主流程：``send`` 只返回 bool，永不抛异常。
- 任何异常消息都不包含 device key。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from ._http import Response, Transport, urllib_transport

DEFAULT_BARK_BASE_URL = "https://api.day.app"
DEFAULT_GROUP = "windalice"


class Notifier(Protocol):
    """通知通道：成功返回 True，失败返回 False（不抛异常）。"""

    def send(self, title: str, body: str) -> bool: ...


@dataclass
class BarkNotifier:
    """通过 Bark 推送 iOS 通知。"""

    key: str
    base_url: str = DEFAULT_BARK_BASE_URL
    group: str = DEFAULT_GROUP
    transport: Transport | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.transport is None:
            self.transport = urllib_transport

    def send(self, title: str, body: str) -> bool:
        payload = json.dumps(
            {"title": title, "body": body, "group": self.group},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        transport: Transport = self.transport  # type: ignore[assignment]
        try:
            response = transport("POST", f"{self.base_url}/{self.key}", headers, payload)
        except OSError:
            return False
        if response.status != 200:
            return False
        return _bark_success(response)


def _bark_success(response: Response) -> bool:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    return code == 200


@dataclass(frozen=True)
class NoopNotifier:
    """没配置 Bark key 时的空通道。"""

    def send(self, title: str, body: str) -> bool:
        return False
