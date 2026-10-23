"""Scripted stand-ins for aiohttp sessions and responses."""

from __future__ import annotations

import json
from typing import Any, Self
from unittest.mock import MagicMock


class FakeResponse:
    """An aiohttp response with a fixed status, body and headers."""

    def __init__(
        self,
        status: int = 200,
        body: bytes | str | object = b"",
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://example.invalid/page",
    ) -> None:
        if isinstance(body, str):
            body = body.encode()
        elif not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.history = ()
        self.content = self

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def text(self) -> str:
        return self._body.decode()

    async def json(self, content_type: Any = None) -> Any:
        return json.loads(self._body)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeSession:
    """Answers each request with the next scripted response or exception."""

    def __init__(self, *responses: FakeResponse | BaseException) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def _next(self, method: str, url: str, **kwargs: Any):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url: str, **kwargs: Any):
        return self._next("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self._next("POST", url, **kwargs)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


def session_factory(session: FakeSession) -> MagicMock:
    """A replacement for ``auth._new_session`` that hands out ``session``."""
    return MagicMock(return_value=session)


INBOX_ROW = {
    "awbBarCode": "1000000101",
    "sender": "",
    "receiver": "",
    "destination": "",
    "status": "In warehouse",
    "date1": None,
    "date2": "2026-10-20T10:00:00",
    "arrivingTimeFrom": "00:00:00",
    "arrivingTimeTo": "00:00:00",
    "statusColor": "#F28230",
    "isSender": False,
    "parcels": 1,
    "envelopes": 0,
    "weight": 1,
    "weightUnit": "kg",
    "amount": 0.0,
    "isPaid": True,
    "iconId": 1,
    "logoIconId": 1,
    "destinationTypeId": 1,
    "destinationTabIconId": 1,
    "tabId": 1,
    "apm": None,
}


def inbox_row(awb: str, **changes: Any) -> dict[str, Any]:
    """A synthetic inbox row for ``awb``."""
    return {**INBOX_ROW, "awbBarCode": awb, **changes}


def trace(awb: str, *events: tuple[str, str], summary: str = "In warehouse") -> dict:
    """A synthetic trace reply with the given ``(date, wording)`` events."""
    return {
        "awbBarcode": awb,
        "statusMessage": summary,
        "events": [
            {"date": date, "event": event, "countyName": "", "localityName": ""}
            for date, event in events
        ],
    }
