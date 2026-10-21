"""CLI 编排行为测试。

覆盖四种真实场景：已领取 / 领取成功 / 凭据失效 / 接口故障。
另外死守一条安全线：日志里不得出现任何凭据值。
"""

from __future__ import annotations

from typing import Any

import pytest

from checkin_windalice.cli import (
    ConfigError,
    Settings,
    _build_client,
    load_settings,
    main,
    run,
)
from checkin_windalice.client import (
    AliceClient,
    ApiError,
    AuthError,
    CheckInStatus,
    ClaimResult,
    Credentials,
)

FAKE_TOKEN = "fake-refresh-token-value"
FAKE_SESSION = "fake-session-id"
FAKE_HEADER_SESSION = "fake-header-session-id"


class FakeClient:
    def __init__(
        self,
        status: CheckInStatus | Exception,
        claim: ClaimResult | Exception | None = None,
    ) -> None:
        self._status = status
        self._claim = claim
        self.claim_calls = 0
        self.status_calls = 0

    def check_in_status(self) -> CheckInStatus:
        self.status_calls += 1
        if isinstance(self._status, Exception):
            raise self._status
        return self._status

    def claim(self) -> ClaimResult:
        self.claim_calls += 1
        if isinstance(self._claim, Exception):
            raise self._claim
        assert self._claim is not None, "测试未预置 claim 结果"
        return self._claim


class RecordingNotifier:
    def __init__(self, result: bool = True) -> None:
        self.messages: list[tuple[str, str]] = []
        self._result = result

    def send(self, title: str, body: str) -> bool:
        self.messages.append((title, body))
        return self._result


def settings() -> Settings:
    return Settings(
        credentials=Credentials(
            refresh_token=FAKE_TOKEN,
            session_id=FAKE_SESSION,
            header_session_id=FAKE_HEADER_SESSION,
        ),
        bark_key="fake-bark-key",
        bark_base_url="https://api.day.app",
    )


CLAIMABLE = CheckInStatus(claimable=True, reason="", reward_points=200)
ALREADY = CheckInStatus(claimable=False, reason="今日已领取", reward_points=200)


def test_already_claimed_skips_claim_and_stays_quiet() -> None:
    client = FakeClient(ALREADY)
    notifier = RecordingNotifier()
    logs: list[str] = []

    code = run(settings(), client=client, notifier=notifier, log=logs.append)

    assert code == 0
    assert client.claim_calls == 0
    assert notifier.messages == []
    assert any("今日已领取" in line for line in logs)


def test_claimable_claims_and_notifies_success() -> None:
    client = FakeClient(CLAIMABLE, ClaimResult(points=200))
    notifier = RecordingNotifier()
    logs: list[str] = []

    code = run(settings(), client=client, notifier=notifier, log=logs.append)

    assert code == 0
    assert client.claim_calls == 1
    title, body = notifier.messages[0]
    assert "领取成功" in title or "领取成功" in body
    assert "200" in body


def test_success_notification_falls_back_to_expected_points() -> None:
    """服务端没回积分时，用 status 里的期望值，别推个空白数字。"""
    client = FakeClient(CLAIMABLE, ClaimResult(points=None))
    notifier = RecordingNotifier()

    run(settings(), client=client, notifier=notifier, log=lambda _: None)

    _, body = notifier.messages[0]
    assert "200" in body


def test_auth_error_notifies_and_exits_1() -> None:
    client = FakeClient(AuthError("凭据已失效（HTTP 401）"))
    notifier = RecordingNotifier()
    logs: list[str] = []

    code = run(settings(), client=client, notifier=notifier, log=logs.append)

    assert code == 1
    title, body = notifier.messages[0]
    assert "失败" in title
    assert "401" in body


def test_api_error_notifies_and_exits_1() -> None:
    client = FakeClient(ApiError("HTTP 500：boom"))
    notifier = RecordingNotifier()

    code = run(settings(), client=client, notifier=notifier, log=lambda _: None)

    assert code == 1


def test_claim_failure_notifies_and_exits_1() -> None:
    client = FakeClient(CLAIMABLE, ApiError("接口返回失败：code=3"))
    notifier = RecordingNotifier()

    code = run(settings(), client=client, notifier=notifier, log=lambda _: None)

    assert code == 1


def test_notifier_failure_does_not_change_exit_code() -> None:
    client = FakeClient(CLAIMABLE, ClaimResult(points=200))
    notifier = RecordingNotifier(result=False)

    code = run(settings(), client=client, notifier=notifier, log=lambda _: None)

    assert code == 0


def test_logs_never_contain_credentials() -> None:
    client = FakeClient(CLAIMABLE, ClaimResult(points=200))
    logs: list[str] = []

    run(settings(), client=client, notifier=RecordingNotifier(), log=logs.append)

    joined = "\n".join(logs)
    assert FAKE_TOKEN not in joined
    assert FAKE_SESSION not in joined
    assert FAKE_HEADER_SESSION not in joined


def test_load_settings_reads_env() -> None:
    env = {
        "WIND_REFRESH_TOKEN": FAKE_TOKEN,
        "WIND_SESSION_ID": FAKE_SESSION,
        "WIND_HEADER_SESSION_ID": FAKE_HEADER_SESSION,
        "BARK_KEY": "k",
    }

    parsed = load_settings(env)

    assert parsed.credentials.refresh_token == FAKE_TOKEN
    assert parsed.credentials.session_id == FAKE_SESSION
    assert parsed.credentials.header_session_id == FAKE_HEADER_SESSION
    assert parsed.bark_key == "k"


def test_load_settings_defaults_bark_base_url() -> None:
    env = {
        "WIND_REFRESH_TOKEN": FAKE_TOKEN,
        "WIND_SESSION_ID": FAKE_SESSION,
        "WIND_HEADER_SESSION_ID": FAKE_HEADER_SESSION,
    }

    assert load_settings(env).bark_key == ""


def test_load_settings_reports_all_missing_names_without_values() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings({"WIND_SESSION_ID": FAKE_SESSION})

    message = str(excinfo.value)
    assert "WIND_REFRESH_TOKEN" in message
    assert "WIND_HEADER_SESSION_ID" in message
    assert FAKE_SESSION not in message


def test_main_returns_2_on_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["checkin-windalice"])

    assert main(env={}) == 2


def test_main_wires_env_into_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_client_factory(settings: Settings) -> FakeClient:
        captured["settings"] = settings
        return FakeClient(ALREADY)

    monkeypatch.setattr("checkin_windalice.cli._build_client", fake_client_factory)
    monkeypatch.setattr(
        "checkin_windalice.cli._build_notifier", lambda settings: RecordingNotifier()
    )

    code = main(
        env={
            "WIND_REFRESH_TOKEN": FAKE_TOKEN,
            "WIND_SESSION_ID": FAKE_SESSION,
            "WIND_HEADER_SESSION_ID": FAKE_HEADER_SESSION,
        }
    )

    assert code == 0
    assert isinstance(captured["settings"], Settings)
    assert captured["settings"].credentials.refresh_token == FAKE_TOKEN


def test_real_client_is_used_by_default() -> None:
    built = _build_client(settings())

    assert isinstance(built, AliceClient)
