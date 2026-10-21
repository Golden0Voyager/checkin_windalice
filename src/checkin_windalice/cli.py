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
from datetime import UTC, date, datetime

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
EXPIRY_ENV = "WIND_CREDENTIAL_EXPIRES_ON"

#: 凭据到期前几天开始提醒。Wind 的登录态上限是 10 天，提前 2 天足够从容重登。
REMINDER_DAYS = 2


class ConfigError(Exception):
    """必需的 Secrets 缺失或格式不对。"""


@dataclass(frozen=True)
class Settings:
    credentials: Credentials
    bark_key: str
    bark_base_url: str = DEFAULT_BARK_BASE_URL
    base_url: str = DEFAULT_BASE_URL
    expires_on: date | None = None


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
        expires_on=_parse_expiry(env.get(EXPIRY_ENV, "")),
    )


def _parse_expiry(raw: str) -> date | None:
    """解析 ``YYYY-MM-DD``；空值表示没配置提醒。"""
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(
            f"{EXPIRY_ENV} 不是合法的日期（要求 YYYY-MM-DD，例如 2026-10-31）"
        ) from exc


def run(
    settings: Settings,
    client: AliceClient,
    notifier: Notifier,
    log: Callable[[str], None] = print,
    today: date | None = None,
) -> int:
    code = _check_in(settings, client, notifier, log)
    _maybe_remind(settings, notifier, log, today)
    return code


def _check_in(
    settings: Settings,
    client: AliceClient,
    notifier: Notifier,
    log: Callable[[str], None],
) -> int:
    del settings  # 客户端已经把凭据握在手里了
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


def _maybe_remind(
    settings: Settings,
    notifier: Notifier,
    log: Callable[[str], None],
    today: date | None,
) -> None:
    """凭据快到期时提醒重登；已过期则交给 401 报警，不重复提醒。"""
    if settings.expires_on is None:
        return
    current = today or datetime.now(UTC).date()
    days_left = (settings.expires_on - current).days
    if not 0 <= days_left <= REMINDER_DAYS:
        return
    log(f"[提醒] 凭据还有 {days_left} 天到期（{settings.expires_on.isoformat()}）")
    notifier.send(
        "Wind Alice 凭据快到期 ⏳",
        f"还有 {days_left} 天到期（{settings.expires_on.isoformat()}）。"
        "请重新登录 alice.wind.com.cn 并更新 Secrets，否则签到会中断。",
    )


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
