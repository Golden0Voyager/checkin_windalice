"""凭据同步到 GitHub Secrets 的行为测试。

死守两条线：写进去的值必须对；日志里绝不能出现值。
"""

from __future__ import annotations

import subprocess
from datetime import date
from typing import Any

import pytest

from checkin_windalice import sync
from checkin_windalice.chrome import ChromeCredentials, CookieError
from checkin_windalice.client import CheckInStatus, Credentials

CREDS = Credentials(
    refresh_token="refresh-secret-value",
    session_id="session-secret-value",
    header_session_id="header-secret-value",
)
EXPIRY = date(2026, 10, 31)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str] | Any, stdin: str | None) -> None:
        self.calls.append((list(args), stdin))


def test_push_writes_three_secrets_and_expiry_variable() -> None:
    runner = RecordingRunner()

    sync.push(CREDS, EXPIRY, repo="me/repo", run=runner, log=lambda _: None)

    secret_calls = [(args, stdin) for args, stdin in runner.calls if args[0] == "secret"]
    assert [args[2] for args, _ in secret_calls] == [
        "WIND_REFRESH_TOKEN",
        "WIND_SESSION_ID",
        "WIND_HEADER_SESSION_ID",
    ]
    assert [stdin for _, stdin in secret_calls] == [
        "refresh-secret-value",
        "session-secret-value",
        "header-secret-value",
    ]
    assert all("--repo" in args and "me/repo" in args for args, _ in secret_calls)

    variable_calls = [args for args, _ in runner.calls if args[0] == "variable"]
    assert variable_calls == [
        [
            "variable",
            "set",
            "WIND_CREDENTIAL_EXPIRES_ON",
            "--body",
            "2026-10-31",
            "--repo",
            "me/repo",
        ]
    ]


def test_push_never_logs_secret_values() -> None:
    lines: list[str] = []

    sync.push(CREDS, EXPIRY, repo="me/repo", run=RecordingRunner(), log=lines.append)

    joined = "\n".join(lines)
    assert "refresh-secret-value" not in joined
    assert "session-secret-value" not in joined
    assert "header-secret-value" not in joined
    assert "WIND_REFRESH_TOKEN" in joined


def test_gh_failure_becomes_sync_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="HTTP 403: Resource not accessible"
        )

    monkeypatch.setattr(sync.subprocess, "run", failing)

    with pytest.raises(sync.SyncError, match="403"):
        sync._run_gh(["secret", "set", "X", "--repo", "me/repo"], "value")


def test_missing_gh_binary_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(sync.subprocess, "run", missing)

    with pytest.raises(sync.SyncError, match="GitHub CLI"):
        sync._run_gh(["secret", "set", "X"], "value")


def test_verify_reports_claimable_state(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, credentials: Credentials) -> None:
            self.credentials = credentials

        def check_in_status(self) -> CheckInStatus:
            return CheckInStatus(claimable=True, reason="", reward_points=200)

    monkeypatch.setattr(sync, "AliceClient", FakeClient)
    lines: list[str] = []

    assert sync.verify(CREDS, log=lines.append) is True
    assert "可领取" in lines[0]


def test_verify_returns_false_when_credentials_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def __init__(self, credentials: Credentials) -> None:
            pass

        def check_in_status(self) -> CheckInStatus:
            raise sync.AliceError("凭据已失效（HTTP 401）")

    monkeypatch.setattr(sync, "AliceClient", FailingClient)
    lines: list[str] = []

    assert sync.verify(CREDS, log=lines.append) is False
    assert "自检失败" in lines[0]


def test_console_entry_exits_two_when_cookies_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_profile: Any) -> ChromeCredentials:
        raise CookieError("cookie 库里没有 weaver-auth.refresh-token")

    monkeypatch.setattr(sync, "read_credentials", boom)

    with pytest.raises(SystemExit) as excinfo:
        sync.console_entry()

    assert excinfo.value.code == 2


def test_console_entry_exits_one_when_push_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sync,
        "read_credentials",
        lambda _profile: ChromeCredentials(credentials=CREDS, expires_on=EXPIRY),
    )

    def failing(*args: object, **kwargs: object) -> None:
        raise sync.SyncError("gh secret set 失败")

    monkeypatch.setattr(sync, "push", failing)

    with pytest.raises(SystemExit) as excinfo:
        sync.console_entry()

    assert excinfo.value.code == 1


def test_console_entry_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sync,
        "read_credentials",
        lambda _profile: ChromeCredentials(credentials=CREDS, expires_on=EXPIRY),
    )
    recorded: list[tuple[Credentials, date, str]] = []
    monkeypatch.setattr(
        sync,
        "push",
        lambda credentials, expires_on, repo: recorded.append((credentials, expires_on, repo)),
    )
    monkeypatch.setattr(sync, "verify", lambda credentials: True)

    sync.console_entry()

    assert recorded == [(CREDS, EXPIRY, sync.DEFAULT_REPO)]
