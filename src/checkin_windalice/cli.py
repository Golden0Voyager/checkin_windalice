"""命令行入口：查状态 → 需要就领取 → 结果推 Bark。

退出码约定：
- ``0`` 正常（无论是新领取还是今天已经领过）
- ``1`` 签到失败（凭据失效、接口故障、网络问题）
- ``2`` 配置缺失（Secrets 没配好）
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .client import (
    DEFAULT_BASE_URL,
    AliceClient,
    AliceError,
    ApiError,
    AuthError,
    Credentials,
)
from .notify import DEFAULT_BARK_BASE_URL, BarkNotifier, NoopNotifier, Notifier

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

REQUIRED_ENV = ("WIND_REFRESH_TOKEN", "WIND_SESSION_ID", "WIND_HEADER_SESSION_ID")


class ConfigError(Exception):
    """必需的 Secrets 缺失。"""


@dataclass(frozen=True)
class Settings:
    credentials: Credentials
    bark_key: str
    bark_base_url: str = DEFAULT_BARK_BASE_URL
    base_url: str = DEFAULT_BASE_URL


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise ConfigError(
            "缺少必要的 GitHub Secrets：" + ", ".join(missing) + "（值不会打印，请到仓库设置里补）"
        )
    return Settings(
        credentials=Credentials(
            refresh_token=env["WIND_REFRESH_TOKEN"],
            session_id=env["WIND_SESSION_ID"],
            header_session_id=env["WIND_HEADER_SESSION_ID"],
        ),
        bark_key=env.get("BARK_KEY", ""),
        bark_base_url=env.get("BARK_BASE_URL") or DEFAULT_BARK_BASE_URL,
        base_url=env.get("WIND_BASE_URL") or DEFAULT_BASE_URL,
    )


def run(
    settings: Settings,
    client: AliceClient,
    notifier: Notifier,
    log: Callable[[str], None] = print,
) -> int:
    try:
        status = client.check_in_status()
    except AuthError as exc:
        log(f"[失败] {exc}")
        notifier.send("Wind Alice 签到失败 · 凭据失效", f"{exc}")
        return EXIT_FAILED
    except ApiError as exc:
        log(f"[失败] {exc}")
        notifier.send("Wind Alice 签到失败", f"{exc}")
        return EXIT_FAILED

    if not status.claimable:
        log(f"[跳过] 今天无需领取：{status.reason or '状态不可领取'}")
        return EXIT_OK

    log(f"[待领取] 今日签到奖励 {status.reward_points} 积分，开始领取")
    try:
        result = client.claim()
    except AliceError as exc:
        log(f"[失败] 领取失败：{exc}")
        notifier.send("Wind Alice 签到失败", f"{exc}")
        return EXIT_FAILED

    points = result.points if result.points is not None else status.reward_points
    log(f"[成功] 已领取 {points} 积分")
    notifier.send("Wind Alice 领取成功 🎉", f"今日签到到账 {points} 积分")
    return EXIT_OK


def _build_client(settings: Settings) -> AliceClient:
    return AliceClient(credentials=settings.credentials, base_url=settings.base_url)


def _build_notifier(settings: Settings) -> Notifier:
    if not settings.bark_key:
        return NoopNotifier()
    return BarkNotifier(key=settings.bark_key, base_url=settings.bark_base_url)


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    del argv  # 目前没有命令行参数
    try:
        settings = load_settings(env if env is not None else {})
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    return run(
        settings,
        client=_build_client(settings),
        notifier=_build_notifier(settings),
    )


def console_entry() -> None:
    """GitHub Actions 用的入口：从真实环境变量读配置。"""
    import os

    raise SystemExit(main(env=os.environ))
