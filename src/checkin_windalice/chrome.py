"""从本机 Chrome 的 cookie 库里取出当前有效的 Wind 凭据。

为什么需要它：Wind 的 ``refresh-token`` / ``session-id`` 是 httpOnly cookie，页面 JS 读不到，
只能在浏览器里 F12 手抄。但它们是本机文件里的一份加密记录 —— Chrome 用 Keychain 里的
「Chrome Safe Storage」密码派生密钥做 AES-128-CBC 加密，所以同一台 Mac 上可以直接解开。

这样把「重登后复制三个值」压缩成一条命令，也顺手拿到了真实的到期时间。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .client import Credentials

__all__ = [
    "CookieError",
    "ChromeCredentials",
    "default_profile",
    "read_credentials",
]

REFRESH_COOKIE = "weaver-auth.refresh-token"
SESSION_COOKIE = "weaver-auth.session-id"

#: Chrome 的 WebKit 时间戳（1601-01-01 起的微秒）与 Unix 时间的差值。
CHROME_EPOCH_OFFSET = 11_644_473_600

_KEYCHAIN_CMD = (
    "security",
    "find-generic-password",
    "-w",
    "-s",
    "Chrome Safe Storage",
    "-a",
    "Chrome",
)

#: Chrome 用 PBKDF2-HMAC-SHA1 从 Keychain 密码派生 AES-128 密钥，参数是写死的。
_PBKDF2_SALT = b"saltysalt"
_PBKDF2_ROUNDS = 1003
_KEY_BYTES = 16
_AES_IV = b" " * 16


class CookieError(Exception):
    """读不到 cookie，或 cookie 无法解密。消息里绝不包含 cookie 值。"""


@dataclass(frozen=True)
class ChromeCredentials:
    """一份可用的凭据，外加从 cookie 库里读出的真实到期时间。"""

    credentials: Credentials
    expires_on: date


def default_profile() -> Path:
    """macOS 上 ``Profile 1`` 的位置；可用环境变量覆盖。"""
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 1"


def read_credentials(profile: Path) -> ChromeCredentials:
    """读取 ``profile`` 下 Chrome cookie 库里的 Wind 凭据。

    Raises:
        CookieError: cookie 缺失、无法解密，或 Keychain 未授权。
    """
    db_path = profile / "Cookies"
    if not db_path.exists():
        raise CookieError(f"找不到 cookie 库：{db_path}")

    with _snapshot(db_path) as con:
        rows = con.execute(
            "select name, host_key, encrypted_value, value, expires_utc from cookies "
            "where host_key like '%wind%' and name in (?, ?)",
            (REFRESH_COOKIE, SESSION_COOKIE),
        ).fetchall()

    found = {row[0]: row for row in rows}
    missing = [n for n in (REFRESH_COOKIE, SESSION_COOKIE) if n not in found]
    if missing:
        raise CookieError(
            "cookie 库里没有 " + "、".join(missing) + "：请先在浏览器登录 alice.wind.com.cn"
        )

    key = _derived_key()
    values: dict[str, str] = {}
    expires_raw = 0
    for name, host, encrypted, plain, expires_utc in rows:
        values[name] = plain or _decrypt(bytes(encrypted), key, host)
        expires_raw = max(expires_raw, int(expires_utc))

    if any(not value for value in values.values()):
        raise CookieError("解出来的 cookie 是空值，可能已被浏览器清除")

    return ChromeCredentials(
        credentials=Credentials(
            refresh_token=values[REFRESH_COOKIE],
            session_id=values[SESSION_COOKIE],
            # 实测：签名需要的 wind.sessionid 请求头可以直接用 session-id 顶替。
            header_session_id=values[SESSION_COOKIE],
        ),
        expires_on=_chrome_time_to_date(expires_raw),
    )


def _chrome_time_to_date(microseconds: int) -> date:
    seconds = microseconds / 1_000_000 - CHROME_EPOCH_OFFSET
    return datetime.fromtimestamp(seconds, UTC).date()


@contextmanager
def _snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    """给正在运行的 Chrome 拍一份一致的快照，避免读到写了一半的库。"""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "cookies.db"
        try:
            _backup(db_path, target)
        except sqlite3.Error:
            # 极少数情况下（库被独占）退回文件拷贝，WAL 一起带上。
            shutil.copy2(db_path, target)
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(db_path.name + suffix)
                if sidecar.exists():
                    shutil.copy2(sidecar, target.with_name(target.name + suffix))
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            yield con
        finally:
            con.close()


def _backup(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(target)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def _safe_storage_key() -> bytes:
    try:
        out = subprocess.run(_KEYCHAIN_CMD, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CookieError(
            "读不到 Keychain 里的「Chrome Safe Storage」密码："
            "请在钥匙串访问里把该项授权给当前程序（首次会弹窗）"
        ) from exc
    return out.stdout.rstrip(b"\n")


def _derived_key() -> bytes:
    """把 Keychain 密码派生成 AES-128 密钥。"""
    return hashlib.pbkdf2_hmac(
        "sha1", _safe_storage_key(), _PBKDF2_SALT, _PBKDF2_ROUNDS, _KEY_BYTES
    )


def _decrypt(blob: bytes, key: bytes, host: str) -> str:
    if blob[:3] != b"v10":
        raise CookieError(
            f"暂不支持这种 cookie 加密格式（前缀 {blob[:3]!r}），Chrome 可能启用了应用绑定加密"
        )
    try:
        out = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-128-cbc",
                "-K",
                key.hex(),
                "-iv",
                _AES_IV.hex(),
                "-nopad",
            ],
            input=blob[3:],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CookieError("cookie 解密失败（openssl 报错）") from exc

    raw = out.stdout
    if not raw or not 1 <= raw[-1] <= 16:
        raise CookieError("cookie 解密失败（填充不合法）")
    raw = raw[: -raw[-1]]
    digest = hashlib.sha256(host.encode()).digest()
    if raw.startswith(digest):
        raw = raw[len(digest) :]
    try:
        return raw.decode()
    except UnicodeDecodeError as exc:
        raise CookieError("cookie 解密失败（内容不是文本）") from exc
