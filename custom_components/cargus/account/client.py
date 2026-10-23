"""Client for the signed-in MyCargus inbox.

Owns the token lifecycle: a still-valid access token is reused, an expired one
is refreshed, and a refused refresh falls back to a full sign-in with the
stored password. Only a rejected password reaches the user as a reauth.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import time
from typing import Any

import aiohttp

from .auth import (
    CargusAuthError,
    CargusInvalidCredentials,
    CargusTokens,
    async_login,
    async_refresh,
)
from .headers import VARIANTS, build_account_headers

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://cmacwabackend.azurewebsites.net/api"
# shipmentType values the portal walks; their meaning is not documented.
SHIPMENT_TYPES = (0, 1, 2, 3, 4)
LANGUAGE = "EN"
MAX_BODY_BYTES = 1024 * 1024
_REFRESH_MARGIN_SECONDS = 300
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_BOM = b"\xef\xbb\xbf"


class CargusAccountError(Exception):
    """The inbox could not be read this time; retry on the next poll."""

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        """Keep the HTTP status for the coordinator's backoff."""
        super().__init__(f"MyCargus request failed: {detail}")
        self.status_code = status_code


class CargusAccountAuthError(CargusAccountError):
    """The stored password no longer signs in: the user has to reauthenticate."""


def awb_of(item: dict[str, Any]) -> str | None:
    """The AWB of one inbox row, whichever key spelling the row uses."""
    for key in ("awbBarCode", "awbBarcode", "barCode", "barcode", "awb"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _rows(payload: Any) -> list[dict[str, Any]] | None:
    """Return the list of rows inside a reply, or ``None`` for an unknown shape."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "awbs", "result", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    return None


def describe_shape(value: Any, depth: int = 0) -> Any:
    """Keys and value types only, never values, for logs and diagnostics."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"object({len(value)} keys)"
        return {key: describe_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if not value:
            return "list(0)"
        return [f"list({len(value)})", describe_shape(value[0], depth + 1)]
    return type(value).__name__


class CargusAccountClient:
    """Signed-in calls to the MyCargus backend on behalf of one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        phone: str,
        tokens: CargusTokens | None = None,
        on_tokens: Callable[[CargusTokens], None] | None = None,
        login: Callable[[str, str], Awaitable[CargusTokens]] = async_login,
    ) -> None:
        """Store the credentials and any token pair from a previous run."""
        self._session = session
        self._email = email
        self._password = password
        self._phone = phone
        self._tokens = tokens
        self._on_tokens = on_tokens
        self._login = login
        self._lock = asyncio.Lock()
        self.header_variant: str | None = None
        self.last_token_source: str | None = None
        self.shapes: dict[str, Any] = {}
        # Warning kinds already logged since the inbox last answered in full,
        # so a lasting failure warns once instead of on every poll.
        self._warned: set[str] = set()

    async def async_sign_in(self) -> None:
        """Sign in from scratch; used by the config flow to check the input."""
        self._store(await self._login(self._email, self._password), "login")

    def _store(self, tokens: CargusTokens, source: str) -> None:
        self._tokens = tokens
        self.last_token_source = source
        if self._on_tokens is not None:
            self._on_tokens(tokens)

    async def _async_access_token(self, *, force: bool = False) -> str:
        async with self._lock:
            tokens = self._tokens
            if (
                not force
                and tokens is not None
                and tokens.expires_at - _REFRESH_MARGIN_SECONDS > time.time()
            ):
                self.last_token_source = "cache"
                return tokens.access_token
            if tokens is not None and tokens.refresh_token:
                try:
                    refreshed = await async_refresh(tokens.refresh_token)
                except CargusAuthError:
                    pass
                else:
                    self._store(refreshed, "refresh")
                    return refreshed.access_token
            _LOGGER.debug("MyCargus: signing in with the stored password")
            try:
                fresh = await self._login(self._email, self._password)
            except CargusInvalidCredentials as err:
                raise CargusAccountAuthError("password rejected") from err
            except CargusAuthError as err:
                raise CargusAccountError(f"sign-in failed ({err})") from err
            self._store(fresh, "login")
            return fresh.access_token

    async def _async_get(self, path: str, params: dict[str, Any]) -> Any:
        """GET one signed-in route and return the parsed JSON (``None`` if empty).

        A ``401`` renews the token once. A ``400``/``403`` tries the other
        header variant once, since either can be the one the backend accepts.
        """
        token = await self._async_access_token()
        order = list(VARIANTS)
        if self.header_variant:
            order.remove(self.header_variant)
            order.insert(0, self.header_variant)
        renewed = False
        index = 0
        while True:
            variant = order[index]
            status, body = await self._async_request(path, params, variant, token)
            _LOGGER.debug(
                "MyCargus %s %s (headers=%s): HTTP %s, %s bytes",
                path,
                {k: v for k, v in params.items() if k != "BarCode"},
                variant,
                status,
                len(body),
            )
            if status == 401 and not renewed:
                renewed = True
                token = await self._async_access_token(force=True)
                continue
            if status in (400, 403):
                index += 1
                if index < len(order):
                    _LOGGER.debug(
                        "MyCargus refused the %s headers (HTTP %s); trying %s",
                        variant,
                        status,
                        order[index],
                    )
                    continue
                self._warn_once(
                    f"refused:{path}",
                    "MyCargus refused %s with every header variant (HTTP %s); "
                    "check that the phone number matches the one on the account",
                    path,
                    status,
                )
                raise CargusAccountError(f"HTTP {status}", status_code=status)
            if status == 401:
                raise CargusAccountError("HTTP 401 after renewing", status_code=401)
            if status not in (200, 204):
                raise CargusAccountError(f"HTTP {status}", status_code=status)
            self._remember_variant(variant)
            body = body.removeprefix(_BOM).strip()
            if status == 204 or not body:
                return None
            if body.startswith(b"<"):
                raise CargusAccountError("unexpected HTML body")
            try:
                return json.loads(body)
            except ValueError as err:
                raise CargusAccountError("unparseable body") from err

    def _warn_once(self, kind: str, message: str, *args: object) -> None:
        if kind in self._warned:
            _LOGGER.debug(message, *args)
            return
        self._warned.add(kind)
        _LOGGER.warning(message, *args)

    def _remember_variant(self, variant: str) -> None:
        if self.header_variant != variant:
            _LOGGER.debug("MyCargus accepts the %s headers", variant)
        self.header_variant = variant

    async def _async_request(
        self, path: str, params: dict[str, Any], variant: str, token: str
    ) -> tuple[int, bytes]:
        headers = build_account_headers(variant, self._phone, token)
        try:
            async with self._session.get(
                f"{API_BASE}/{path}", params=params, headers=headers, timeout=_TIMEOUT
            ) as response:
                body = bytearray()
                async for chunk in response.content.iter_chunked(16 * 1024):
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        raise CargusAccountError("response too large")
                return response.status, bytes(body)
        except TimeoutError as err:
            raise CargusAccountError("timeout") from err
        except aiohttp.ClientError as err:
            raise CargusAccountError(f"network error ({err!r})") from err

    async def async_get_inbox(self) -> list[dict[str, Any]]:
        """Every row of the account inbox, one per AWB, across the list types.

        Each row gets ``_shipment_type`` (the list it came from) so the
        direction split and the diagnostics can see where it was found. Any
        list failing fails the whole read: a partial inbox would drop parcels
        and announce them as new once the list is back.
        """
        rows: dict[str, dict[str, Any]] = {}
        for shipment_type in SHIPMENT_TYPES:
            params = {"shipmentType": shipment_type, "lang": LANGUAGE}
            payload = await self._async_get("Awbs/AwbList", params)
            found = [] if payload is None else _rows(payload)
            if found is None:
                self._warn_once(
                    "shape:AwbList",
                    "MyCargus AwbList type %s has an unexpected shape: %s",
                    shipment_type,
                    describe_shape(payload),
                )
                raise CargusAccountError("unexpected inbox shape")
            self.shapes[f"AwbList[{shipment_type}]"] = describe_shape(payload)
            _LOGGER.debug(
                "MyCargus AwbList type %s: %s rows, row shape %s",
                shipment_type,
                len(found),
                describe_shape(found[0]) if found else None,
            )
            for row in found:
                awb = awb_of(row)
                if awb is None:
                    _LOGGER.debug(
                        "MyCargus inbox row without an AWB: keys %s", sorted(row)
                    )
                    continue
                rows.setdefault(awb, {**row, "_shipment_type": shipment_type})
        self._warned.clear()
        return list(rows.values())

    async def async_get_full_awb(self, awb: str, *, for_sender: bool) -> Any:
        """The account's detail view of one AWB (probed for its shape only)."""
        payload = await self._async_get(
            "Awbs/FullAwb",
            {"BarCode": awb, "forSender": str(for_sender).lower(), "lang": LANGUAGE},
        )
        self.shapes["FullAwb"] = describe_shape(payload)
        return payload
