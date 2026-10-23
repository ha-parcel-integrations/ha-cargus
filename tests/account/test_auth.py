"""Tests for the scripted MyCargus sign-in and the token refresh."""

import base64
import json
import logging
import time

import aiohttp
import pytest

from custom_components.cargus.account import auth
from custom_components.cargus.account.auth import (
    CargusAuthError,
    CargusInvalidCredentials,
    async_login,
    async_refresh,
    page_settings,
)

from .fakes import FakeResponse, FakeSession, session_factory


def _warnings(caplog, phrase: str) -> int:
    return sum(
        r.levelname == "WARNING" and phrase in r.getMessage() for r in caplog.records
    )


LOGIN_PAGE = (
    "<html><script>var SETTINGS = {"
    '"csrf":"csrf-value","transId":"StateProperties=abc","api":"CombinedSigninAndSignup"'
    "};</script></html>"
)
TOKENS = {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def _confirm(location: str, status: int = 302) -> FakeResponse:
    return FakeResponse(status, headers={"Location": location})


def _login_session(*, confirm=None, asserted=None, token=None) -> FakeSession:
    return FakeSession(
        FakeResponse(200, LOGIN_PAGE),
        asserted or FakeResponse(200, {"status": "200"}),
        confirm or _confirm("https://mycargus.cargus.ro/cb?code=the-code"),
        token or FakeResponse(200, TOKENS),
    )


@pytest.fixture
def use_session(monkeypatch):
    def _use(session: FakeSession) -> FakeSession:
        monkeypatch.setattr(auth, "_new_session", session_factory(session))
        return session

    return _use


def test_page_settings_reads_the_form_state():
    settings = page_settings(LOGIN_PAGE)
    assert settings == {
        "tx": "StateProperties=abc",
        "csrf": "csrf-value",
        "api": "CombinedSigninAndSignup",
    }
    assert page_settings("<html></html>") == {"tx": None, "csrf": None, "api": None}


async def test_login_walks_the_four_steps(use_session):
    session = use_session(_login_session())
    tokens = await async_login("user@example.com", "secret")
    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.expires_at == pytest.approx(time.time() + 3600, abs=5)

    methods = [method for method, _, _ in session.calls]
    assert methods == ["GET", "POST", "GET", "POST"]
    _, asserted_url, asserted = session.calls[1]
    assert "SelfAsserted?tx=StateProperties=abc" in asserted_url
    assert asserted["data"]["signInName"] == "user@example.com"
    assert asserted["headers"]["X-CSRF-TOKEN"] == "csrf-value"
    _, _, token_call = session.calls[3]
    assert token_call["data"]["code"] == "the-code"
    assert token_call["data"]["grant_type"] == "authorization_code"


async def test_login_never_logs_the_secrets(use_session, caplog):
    caplog.set_level(logging.DEBUG)
    use_session(_login_session())
    await async_login("user@example.com", "secret")
    for secret in ("secret", "the-code", "csrf-value", "access", "user@example"):
        assert secret not in caplog.text.replace("access expires", "")


async def test_login_page_error_is_not_a_credential_problem(use_session):
    use_session(FakeSession(FakeResponse(500, "")))
    with pytest.raises(CargusAuthError) as err:
        await async_login("user@example.com", "secret")
    assert not isinstance(err.value, CargusInvalidCredentials)


async def test_login_page_without_form_state(use_session, caplog):
    use_session(FakeSession(FakeResponse(200, "<html></html>")))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")
    assert "no form state" in caplog.text


@pytest.mark.parametrize(
    "asserted",
    [
        FakeResponse(200, {"status": "400", "errorCode": "AADB2C90225"}),
        FakeResponse(400, ""),
    ],
)
async def test_wrong_password_is_invalid_credentials(use_session, asserted):
    use_session(_login_session(asserted=asserted))
    with pytest.raises(CargusInvalidCredentials):
        await async_login("user@example.com", "wrong")


async def test_credentials_step_server_error(use_session):
    use_session(_login_session(asserted=FakeResponse(503, "")))
    with pytest.raises(CargusAuthError) as err:
        await async_login("user@example.com", "secret")
    assert not isinstance(err.value, CargusInvalidCredentials)


async def test_an_extra_step_is_named_in_the_log(use_session, caplog):
    follow_up = FakeResponse(
        200, '<script>var SETTINGS = {"api":"PhoneFactor"};</script>'
    )
    use_session(_login_session(confirm=follow_up))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")
    assert "step=PhoneFactor" in caplog.text


async def test_an_error_redirect_is_logged_without_its_text(use_session, caplog):
    location = (
        "https://mycargus.cargus.ro/cb?error=access_denied"
        "&error_description=AADB2C90118%3A+user+typed+something"
    )
    use_session(_login_session(confirm=_confirm(location)))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")
    assert "access_denied AADB2C90118" in caplog.text
    assert "user typed" not in caplog.text


async def test_confirm_without_location(use_session, caplog):
    use_session(_login_session(confirm=FakeResponse(500, "")))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")
    assert "without an authorization code" in caplog.text


async def test_state_mismatch_is_refused(use_session):
    location = "https://mycargus.cargus.ro/cb?code=c&state=someone-else"
    use_session(_login_session(confirm=_confirm(location)))
    with pytest.raises(CargusAuthError, match="state"):
        await async_login("user@example.com", "secret")


async def test_token_exchange_failure(use_session, caplog):
    token = FakeResponse(
        400, {"error": "invalid_grant", "error_description": "AADB2C90080: expired"}
    )
    use_session(_login_session(token=token))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")
    assert "invalid_grant AADB2C90080" in caplog.text


async def test_token_reply_without_tokens(use_session):
    use_session(_login_session(token=FakeResponse(200, {"access_token": "a"})))
    with pytest.raises(CargusAuthError):
        await async_login("user@example.com", "secret")


async def test_login_network_error_warns_once_until_a_good_sign_in(use_session, caplog):
    for _ in range(2):
        use_session(FakeSession(aiohttp.ClientConnectionError("down")))
        with pytest.raises(CargusAuthError, match="network"):
            await async_login("user@example.com", "secret")
    assert _warnings(caplog, "failed on the network") == 1

    use_session(_login_session())
    await async_login("user@example.com", "secret")
    assert not auth._warned


async def test_refresh_keeps_the_old_refresh_token_when_none_comes_back(use_session):
    use_session(
        FakeSession(FakeResponse(200, {"access_token": "new", "expires_in": "x"}))
    )
    tokens = await async_refresh("old-refresh")
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "old-refresh"
    assert tokens.expires_at == pytest.approx(time.time() + 3600, abs=5)


async def test_refresh_reads_a_jwt_expiry(use_session, caplog):
    caplog.set_level(logging.DEBUG)
    refresh = _jwt(int(time.time()) + 600)
    use_session(
        FakeSession(FakeResponse(200, {"access_token": "a", "refresh_token": refresh}))
    )
    await async_refresh("old")
    assert "refresh rotated=True" in caplog.text
    assert "refresh JWT exp in" in caplog.text


@pytest.mark.parametrize(
    "answer",
    [
        FakeResponse(400, {"error": "invalid_grant"}),
        FakeResponse(400, "not json"),
        FakeResponse(400, [1]),
        FakeResponse(200, [1]),
        aiohttp.ClientConnectionError("down"),
    ],
)
async def test_refresh_failures_are_auth_errors(use_session, answer):
    use_session(FakeSession(answer))
    with pytest.raises(CargusAuthError):
        await async_refresh("old")


def test_jwt_expiry_is_none_for_opaque_tokens():
    assert auth._jwt_exp("opaque") is None
    assert auth._jwt_exp("a.!!!.c") is None
    assert auth._jwt_exp(_jwt(123)) == 123


async def test_the_default_session_is_a_real_one():
    session = auth._new_session(aiohttp.DummyCookieJar())
    assert isinstance(session, aiohttp.ClientSession)
    await session.close()
