"""MyCargus sign-in: a scripted Azure AD B2C login and the token refresh.

Every step logs what it saw at debug level — step name, HTTP status, which
page B2C served — and never a password, token, code or cookie value.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import html
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse
import uuid

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Warnings already logged since the last good sign-in, keyed on the message,
# so a lasting failure warns once and then drops to debug.
_warned: set[str] = set()


def _warn(message: str, *args: object) -> None:
    if message in _warned:
        _LOGGER.debug(message, *args)
        return
    _warned.add(message)
    _LOGGER.warning(message, *args)


_HOST = "https://mycargus.b2clogin.com"
_TENANT = "myCargus.onmicrosoft.com"
_POLICY = "B2C_1A_ACCOUNTLINK_SUSI_WEB"
_CLIENT_ID = "0cbd5f1a-3ab9-40d6-b8ac-3c0ae62d23af"
_SCOPE = (
    "https://myCargus.onmicrosoft.com/d12878e2-9602-447e-bd09-6e32e1d1d4bb/"
    "API.Access offline_access openid profile"
)
_REDIRECT_URI = "https://mycargus.cargus.ro/authentication/login-callback"
_AUTHORIZE_URL = f"{_HOST}/{_TENANT}/{_POLICY}/oauth2/v2.0/authorize"
_TOKEN_URL = f"{_HOST}/{_TENANT}/{_POLICY}/oauth2/v2.0/token"
# suite: allow browser user-agent (the sign-in pages are the MyCargus website, scripted as a browser)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_DEFAULT_EXPIRES_IN = 3600


def _new_session(cookie_jar: aiohttp.abc.AbstractCookieJar) -> aiohttp.ClientSession:
    """A short-lived session of its own, kept apart from Home Assistant's."""
    return aiohttp.ClientSession(cookie_jar=cookie_jar, timeout=_TIMEOUT)


class CargusAuthError(Exception):
    """Sign-in failed for a reason other than the user's credentials."""


class CargusInvalidCredentials(CargusAuthError):
    """MyCargus rejected the e-mail address or password."""


@dataclass
class CargusTokens:
    """An access/refresh token pair and when the access token expires."""

    access_token: str
    refresh_token: str
    expires_at: float


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _find(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return (
                html.unescape(match.group(1))
                .replace("\\u0026", "&")
                .replace("\\/", "/")
            )
    return None


def page_settings(text: str) -> dict[str, str | None]:
    """Pull the transaction, csrf token and step name out of a B2C page."""
    decoded = html.unescape(text).replace("\\u0026", "&").replace("\\/", "/")
    return {
        "tx": _find(
            (
                r'["\']transId["\']\s*:\s*["\']([^"\']+)["\']',
                r"tx=(StateProperties=[^\"&'<>\s]+)",
            ),
            decoded,
        ),
        "csrf": _find(
            (
                r'["\']csrf["\']\s*:\s*["\']([^"\']+)["\']',
                r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
            ),
            decoded,
        ),
        "api": _find((r'["\']api["\']\s*:\s*["\']([^"\']+)["\']',), decoded),
    }


def _query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


def _jwt_exp(token: str) -> int | None:
    """Read ``exp`` from a JWT without validating it, for logging only."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        return int(json.loads(base64.urlsafe_b64decode(segment))["exp"])
    except (ValueError, KeyError, TypeError):
        return None


def _tokens_from(payload: Any, *, fallback_refresh: str | None = None) -> CargusTokens:
    if not isinstance(payload, dict):
        raise CargusAuthError("token reply is not an object")
    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or fallback_refresh or "").strip()
    if not access or not refresh:
        _warn("MyCargus token reply misses a token (keys: %s)", sorted(payload))
        raise CargusAuthError("token reply without tokens")
    try:
        expires_in = int(payload.get("expires_in") or _DEFAULT_EXPIRES_IN)
    except (TypeError, ValueError):
        expires_in = _DEFAULT_EXPIRES_IN
    now = time.time()
    refresh_exp = _jwt_exp(refresh)
    _LOGGER.debug(
        "MyCargus tokens: access expires in %ss, refresh_token_expires_in=%s, "
        "refresh JWT exp in %s, refresh rotated=%s",
        expires_in,
        payload.get("refresh_token_expires_in"),
        f"{refresh_exp - int(now)}s" if refresh_exp else "n/a (opaque)",
        fallback_refresh is not None and refresh != fallback_refresh,
    )
    _warned.clear()
    return CargusTokens(access, refresh, now + expires_in)


async def _token_error(response: aiohttp.ClientResponse) -> str:
    try:
        payload = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return "non-JSON body"
    if not isinstance(payload, dict):
        return "unexpected body"
    description = " ".join(str(payload.get("error_description") or "").split())
    # B2C prefixes the description with its error code; the rest can echo input.
    code = description.split(":", 1)[0][:40] if description else ""
    return f"{payload.get('error')} {code}".strip()


async def async_login(email: str, password: str) -> CargusTokens:
    """Sign in with e-mail and password and return a fresh token pair.

    Runs on its own short-lived session: the B2C steps are tied together by
    cookies that must not leak into Home Assistant's shared session.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(24))
    authorize_url = (
        _AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": _CLIENT_ID,
                "redirect_uri": _REDIRECT_URI,
                "response_type": "code",
                "scope": _SCOPE,
                "state": state,
                "nonce": _b64url(secrets.token_bytes(16)),
                "prompt": "login",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "client-request-id": str(uuid.uuid4()),
                "response_mode": "query",
            },
            quote_via=quote,
        )
    )
    browser = {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    jar = aiohttp.CookieJar(quote_cookie=False)
    async with _new_session(jar) as session:
        try:
            return await _async_login_steps(
                session, jar, authorize_url, browser, email, password, state, verifier
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            _warn("MyCargus sign-in failed on the network: %r", err)
            raise CargusAuthError("network error") from err


async def _async_login_steps(
    session: aiohttp.ClientSession,
    jar: aiohttp.CookieJar,
    authorize_url: str,
    browser: dict[str, str],
    email: str,
    password: str,
    state: str,
    verifier: str,
) -> CargusTokens:
    async with session.get(
        authorize_url,
        headers={**browser, "Accept": "text/html,application/xhtml+xml"},
    ) as response:
        page = await response.text()
        page_url = str(response.url)
        _LOGGER.debug(
            "MyCargus sign-in 1/4 authorize: HTTP %s, %s bytes, redirects=%s",
            response.status,
            len(page),
            len(response.history),
        )
        if response.status >= 400:
            _warn("MyCargus sign-in: the login page answered HTTP %s", response.status)
            raise CargusAuthError(f"authorize HTTP {response.status}")

    settings = page_settings(page)
    tx = settings["tx"] or _query_value(page_url, "tx")
    csrf = settings["csrf"]
    if not csrf:
        for cookie in jar:
            if cookie.key == "x-ms-cpim-csrf":
                csrf = cookie.value
    _LOGGER.debug(
        "MyCargus sign-in 1/4: page step=%s, has tx=%s, has csrf=%s, cookies=%s",
        settings["api"],
        bool(tx),
        bool(csrf),
        sorted({cookie.key for cookie in jar}),
    )
    if not tx or not csrf:
        _warn(
            "MyCargus sign-in: the login page has no form state (step=%s, "
            "tx=%s, csrf=%s); the login page may have changed",
            settings["api"],
            bool(tx),
            bool(csrf),
        )
        raise CargusAuthError("login page without form state")

    base = f"{_HOST}/{_TENANT}/{_POLICY}"
    async with session.post(
        f"{base}/SelfAsserted?tx={quote(tx, safe='=')}&p={_POLICY}",
        headers={
            **browser,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf,
            "Origin": _HOST,
            "Referer": page_url,
        },
        data={"request_type": "RESPONSE", "signInName": email, "password": password},
        allow_redirects=False,
    ) as response:
        body = await response.text()
        status = response.status
    asserted = _json_or_none(body)
    asserted_status = str((asserted or {}).get("status") or "")
    _LOGGER.debug(
        "MyCargus sign-in 2/4 credentials: HTTP %s, reply status=%s, errorCode=%s",
        status,
        asserted_status or None,
        (asserted or {}).get("errorCode"),
    )
    if status in (400, 401, 403):
        raise CargusInvalidCredentials(f"SelfAsserted HTTP {status}")
    if status >= 400:
        raise CargusAuthError(f"SelfAsserted HTTP {status}")
    if asserted_status and asserted_status != "200":
        _LOGGER.info(
            "MyCargus rejected the sign-in (status=%s, errorCode=%s)",
            asserted_status,
            (asserted or {}).get("errorCode"),
        )
        raise CargusInvalidCredentials(f"SelfAsserted status {asserted_status}")

    async with session.get(
        f"{base}/api/CombinedSigninAndSignup/confirmed"
        f"?rememberMe=false&csrf_token={quote(csrf, safe='')}"
        f"&tx={quote(tx, safe='=')}&p={_POLICY}",
        headers={**browser, "Accept": "text/html", "Referer": page_url},
        allow_redirects=False,
    ) as response:
        location = response.headers.get("Location", "")
        status = response.status
        follow_up = await response.text() if status == 200 else ""
    code = _query_value(location, "code") if location else None
    _LOGGER.debug(
        "MyCargus sign-in 3/4 confirm: HTTP %s, redirect to %s, has code=%s, error=%s",
        status,
        urlparse(location).netloc or None,
        bool(code),
        _query_value(location, "error") if location else None,
    )
    if not code:
        if location and _query_value(location, "error"):
            description = _query_value(location, "error_description") or ""
            _warn(
                "MyCargus sign-in was refused after the password step: %s %s",
                _query_value(location, "error"),
                description.split(":", 1)[0][:40],
            )
        elif follow_up:
            _warn(
                "MyCargus asks for another sign-in step (step=%s) that this "
                "integration cannot answer; open a GitHub issue with this line",
                page_settings(follow_up)["api"],
            )
        else:
            _warn(
                "MyCargus sign-in ended without an authorization code (HTTP %s)",
                status,
            )
        raise CargusAuthError("no authorization code")
    if _query_value(location, "state") not in (None, state):
        raise CargusAuthError("state mismatch")

    async with session.post(
        _TOKEN_URL,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        data={
            "client_id": _CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPE,
        },
    ) as response:
        _LOGGER.debug("MyCargus sign-in 4/4 token: HTTP %s", response.status)
        if response.status >= 400:
            _warn(
                "MyCargus token exchange failed: HTTP %s %s",
                response.status,
                await _token_error(response),
            )
            raise CargusAuthError(f"token HTTP {response.status}")
        payload = await response.json(content_type=None)
    return _tokens_from(payload)


async def async_refresh(refresh_token: str) -> CargusTokens:
    """Trade the refresh token for a new pair; raise ``CargusAuthError`` if not.

    Its own cookie-free session, like the sign-in: B2C cookies must not build
    up in Home Assistant's shared jar.
    """
    try:
        async with (
            _new_session(aiohttp.DummyCookieJar()) as session,
            session.post(
                _TOKEN_URL,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                data={
                    "client_id": _CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": _SCOPE,
                },
            ) as response,
        ):
            if response.status >= 400:
                _LOGGER.info(
                    "MyCargus token refresh refused: HTTP %s %s; signing in again",
                    response.status,
                    await _token_error(response),
                )
                raise CargusAuthError(f"refresh HTTP {response.status}")
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.info("MyCargus token refresh failed: %r", err)
        raise CargusAuthError("refresh failed") from err
    _LOGGER.debug("MyCargus token refreshed")
    return _tokens_from(payload, fallback_refresh=refresh_token)


def _json_or_none(body: str) -> dict | None:
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
