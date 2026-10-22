"""Chrome cookie 库读取测试。

用真实的 Chrome 加密格式（AES-128-CBC + 32 字节 host 哈希前缀）造数据，
只把 Keychain 那一步换成固定密钥——解密链路本身是真的。
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from checkin_windalice import chrome

PASSWORD = b"test-safe-storage-password"
KEY = hashlib.pbkdf2_hmac("sha1", PASSWORD, b"saltysalt", 1003, 16)
HOST = ".wind.com.cn"
IV = (b" " * 16).hex()


def encrypt(value: str, key: bytes = KEY, host: str = HOST) -> bytes:
    """按 Chrome 的格式加密一个 cookie 值。"""
    plain = hashlib.sha256(host.encode()).digest() + value.encode()
    proc = subprocess.run(
        ["openssl", "enc", "-e", "-aes-128-cbc", "-K", key.hex(), "-iv", IV],
        input=plain,
        capture_output=True,
        check=True,
    )
    return b"v10" + proc.stdout


def chrome_time(moment: datetime) -> int:
    return int((moment.timestamp() + chrome.CHROME_EPOCH_OFFSET) * 1_000_000)


def build_db(profile: Path, cookies: dict[str, str], expires: datetime) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute(
        "create table cookies (name text, host_key text, encrypted_value blob, "
        "value text, expires_utc integer)"
    )
    for name, value in cookies.items():
        con.execute(
            "insert into cookies values (?,?,?,?,?)",
            (name, HOST, encrypt(value), "", chrome_time(expires)),
        )
    con.commit()
    con.close()


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    return tmp_path / "Profile 1"


def test_read_credentials_decrypts_both_cookies(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expires = datetime(2026, 10, 31, 23, 49, tzinfo=UTC)
    build_db(
        profile,
        {chrome.REFRESH_COOKIE: "refresh-value", chrome.SESSION_COOKIE: "session-value"},
        expires,
    )
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    found = chrome.read_credentials(profile)

    assert found.credentials.refresh_token == "refresh-value"
    assert found.credentials.session_id == "session-value"
    assert found.credentials.header_session_id == "session-value"
    assert found.expires_on == date(2026, 10, 31)


def test_read_credentials_prefers_plaintext_column_when_present(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_db(
        profile,
        {chrome.REFRESH_COOKIE: "encrypted", chrome.SESSION_COOKIE: "encrypted-2"},
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    con = sqlite3.connect(profile / "Cookies")
    con.execute("update cookies set value = 'plain' where name = ?", (chrome.REFRESH_COOKIE,))
    con.commit()
    con.close()
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    found = chrome.read_credentials(profile)

    assert found.credentials.refresh_token == "plain"


def test_missing_database_raises(profile: Path) -> None:
    with pytest.raises(chrome.CookieError, match="找不到 cookie 库"):
        chrome.read_credentials(profile)


def test_missing_cookie_raises(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_db(profile, {chrome.REFRESH_COOKIE: "only-refresh"}, datetime(2026, 11, 1, tzinfo=UTC))
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    with pytest.raises(chrome.CookieError, match="session-id"):
        chrome.read_credentials(profile)


def test_empty_decrypted_value_raises(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_db(
        profile,
        {chrome.REFRESH_COOKIE: "", chrome.SESSION_COOKIE: "session"},
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    with pytest.raises(chrome.CookieError, match="空值"):
        chrome.read_credentials(profile)


def test_unknown_encryption_prefix_raises(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(profile / "Cookies")
    con.execute(
        "create table cookies (name text, host_key text, encrypted_value blob, "
        "value text, expires_utc integer)"
    )
    for name in (chrome.REFRESH_COOKIE, chrome.SESSION_COOKIE):
        con.execute(
            "insert into cookies values (?,?,?,?,?)",
            (name, HOST, b"v20" + b"x" * 32, "", chrome_time(datetime(2026, 11, 1, tzinfo=UTC))),
        )
    con.commit()
    con.close()
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    with pytest.raises(chrome.CookieError, match="加密格式"):
        chrome.read_credentials(profile)


def test_keychain_failure_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(44, "security")

    monkeypatch.setattr(chrome.subprocess, "run", boom)

    with pytest.raises(chrome.CookieError, match="Keychain"):
        chrome._safe_storage_key()


def test_corrupt_blob_is_rejected() -> None:
    with pytest.raises(chrome.CookieError, match="填充"):
        chrome._decrypt(b"v10" + b"\x00" * 16, KEY, HOST)


def test_default_profile_lives_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert chrome.default_profile() == (
        tmp_path / "Library/Application Support/Google/Chrome/Profile 1"
    )


def test_snapshot_falls_back_to_file_copy_when_backup_fails(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_db(
        profile,
        {chrome.REFRESH_COOKIE: "r", chrome.SESSION_COOKIE: "s"},
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(chrome, "_safe_storage_key", lambda: PASSWORD)

    def boom(source: Path, target: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(chrome, "_backup", boom)

    found = chrome.read_credentials(profile)

    assert found.credentials.refresh_token == "r"


def test_openssl_failure_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "openssl")

    monkeypatch.setattr(chrome.subprocess, "run", boom)

    with pytest.raises(chrome.CookieError, match="openssl"):
        chrome._decrypt(b"v10" + b"\x00" * 16, KEY, HOST)


def test_non_text_payload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        stdout = b"\xff\xfe\x01"

    monkeypatch.setattr(chrome.subprocess, "run", lambda *a, **k: FakeProc())

    with pytest.raises(chrome.CookieError, match="不是文本"):
        chrome._decrypt(b"v10" + b"\x00" * 16, KEY, HOST)
