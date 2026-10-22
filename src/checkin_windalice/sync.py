"""把本机 Chrome 里的当前凭据推送到 GitHub Secrets，并顺手自检一次。

Wind 的凭据会被浏览器轮换：你在网页版用一次，服务端就换一套新票，旧票立刻作废。
所以「签到失败」之后不需要打开 DevTools 手抄，跑这一条命令即可：

    uv run checkin-sync
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from .chrome import ChromeCredentials, CookieError, default_profile, read_credentials
from .client import AliceClient, AliceError, Credentials

__all__ = ["DEFAULT_REPO", "SyncError", "console_entry", "push", "verify"]

DEFAULT_REPO = "Golden0Voyager/checkin_windalice"

SECRET_ENV = (
    ("WIND_REFRESH_TOKEN", "refresh_token"),
    ("WIND_SESSION_ID", "session_id"),
    ("WIND_HEADER_SESSION_ID", "header_session_id"),
)

GhRunner = Callable[[Sequence[str], str | None], None]


class SyncError(Exception):
    """推送 GitHub Secrets 失败。"""


def push(
    credentials: Credentials,
    expires_on: date,
    repo: str = DEFAULT_REPO,
    run: GhRunner | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """写入三个 Secret 与到期日变量。日志只出现长度与哈希前缀，不出现值。"""
    runner = run or _run_gh
    for name, field in SECRET_ENV:
        value: str = getattr(credentials, field)
        runner(["secret", "set", name, "--repo", repo], value)
        log(f"[ok] {name:24} len={len(value)}")
    runner(
        [
            "variable",
            "set",
            "WIND_CREDENTIAL_EXPIRES_ON",
            "--body",
            expires_on.isoformat(),
            "--repo",
            repo,
        ],
        None,
    )
    log(f"[ok] WIND_CREDENTIAL_EXPIRES_ON = {expires_on.isoformat()}")


def verify(credentials: Credentials, log: Callable[[str], None] = print) -> bool:
    """用刚推上去的凭据打一次 status，确认上级网关认这份票。"""
    client = AliceClient(credentials)
    try:
        status = client.check_in_status()
    except AliceError as exc:
        log(f"[警告] 同步后的自检失败：{exc}")
        return False
    state = "可领取" if status.claimable else f"不可领取（{status.reason or '未知'}）"
    log(f"[ok] 自检通过：{state}，奖励 {status.reward_points} 积分")
    return True


def _run_gh(args: Sequence[str], stdin: str | None) -> None:
    try:
        proc = subprocess.run(
            ["gh", *args], input=stdin, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise SyncError("找不到 gh 命令，请先安装 GitHub CLI") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        hint = detail[-1][:120] if detail else f"退出码 {proc.returncode}"
        raise SyncError(f"gh {' '.join(args[:3])} 失败：{hint}")


def _mask(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def console_entry() -> None:
    """``checkin-sync`` 的入口。退出码：0 成功，1 推送失败，2 读不到本地 cookie。"""
    profile = Path(os.environ.get("WIND_CHROME_PROFILE") or default_profile())
    repo = os.environ.get("WIND_GITHUB_REPO") or DEFAULT_REPO

    try:
        found: ChromeCredentials = read_credentials(profile)
    except CookieError as exc:
        print(f"[失败] {exc}")
        raise SystemExit(2) from exc

    print(
        f"[读取] {profile.name}  refresh-token sha={_mask(found.credentials.refresh_token)}"
        f"  到期={found.expires_on.isoformat()}"
    )
    try:
        push(found.credentials, found.expires_on, repo=repo)
    except SyncError as exc:
        print(f"[失败] {exc}")
        raise SystemExit(1) from exc

    verify(found.credentials)
