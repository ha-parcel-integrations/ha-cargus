"""Cargus tracking client.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw reply dict for a found parcel,
* returns ``None`` when Cargus answers empty (unknown, not yet scanned, or an
  old parcel that has expired) — a normal state, never an error,
* raises :class:`CargusGateError` when the request is refused outright,
* raises :class:`CargusApiError` for anything else, with ``status_code`` set
  on a non-2xx response and ``retry_after`` set when a 429 carries seconds,
* lets ``aiohttp.ClientError`` propagate untouched.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from ..const import TRACKING_API_URL
from .headers import build_request_headers, new_message

MAX_BODY_BYTES = 256 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_BOM = b"\xef\xbb\xbf"
_GATE_STATUSES = frozenset({400, 401, 403})


class CargusApiError(Exception):
    """Raised when a Cargus API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Cargus API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class CargusGateError(CargusApiError):
    """Raised when Cargus refuses the request itself, for every number alike.

    Not a statement about one parcel, so it must never be read as not-found.
    """


class CargusApiClient:
    """Client for the anonymous Cargus tracking endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details, or ``None`` when Cargus has none."""
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        try:
            headers = build_request_headers(new_message())
        except ValueError as err:
            raise CargusGateError("request headers unavailable") from err

        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                if status == 429:
                    raise CargusApiError(
                        "HTTP 429",
                        status_code=429,
                        retry_after=_parse_retry_after(
                            response.headers.get("Retry-After")
                        ),
                    )
                if status in _GATE_STATUSES:
                    raise CargusGateError(f"HTTP {status}", status_code=status)
                if status in (404, 410):
                    raise CargusApiError(
                        f"HTTP {status} (endpoint moved)", status_code=status
                    )
                if status == 204:
                    return None
                if status != 200:
                    raise CargusApiError(f"HTTP {status}", status_code=status)
                body = await _read_capped(response)
        except TimeoutError as err:
            raise CargusApiError("timeout") from err

        return _parse_body(body, tracking_code)


def _parse_retry_after(value: str | None) -> float | None:
    """Return ``Retry-After`` as seconds, or ``None`` for absent or an HTTP-date."""
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def _read_capped(response: aiohttp.ClientResponse) -> bytes:
    """Read the body, refusing anything larger than :data:`MAX_BODY_BYTES`."""
    body = bytearray()
    async for chunk in response.content.iter_chunked(16 * 1024):
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise CargusApiError("response too large")
    return bytes(body)


def _parse_body(body: bytes, tracking_code: str) -> dict[str, Any] | None:
    """Turn a 200 body into the reply dict, ``None`` when it is empty."""
    body = body.removeprefix(_BOM).strip()
    if not body:
        return None
    if body.startswith(b"<"):
        raise CargusApiError("unexpected HTML body")
    try:
        payload = json.loads(body)
    except ValueError as err:
        raise CargusApiError("unparseable body") from err
    if not isinstance(payload, dict):
        raise CargusApiError("unexpected body (not a JSON object)")
    if str(payload.get("awbBarcode")) != tracking_code:
        raise CargusApiError("reply is for a different parcel")
    return payload
