"""Inbox rows to canonical parcels (pure, no I/O).

The inbox only says which AWBs belong to the account and which side the user
is on; status and history come from the same trace the tracking source reads,
so both sources map wordings identically.
"""

from __future__ import annotations

from datetime import date, datetime, time
import logging
from typing import Any
from zoneinfo import ZoneInfo

from ..const import ParcelStatus
from ..tracking.parcels import normalize_parcel, parse_wall_clock
from .client import awb_of

_LOGGER = logging.getLogger(__name__)

_SUMMARY_KEYS = ("statusMessage", "status", "statusName", "lastStatus")

_CLOCK = ZoneInfo("Europe/Bucharest")
_NO_TIME = time(0, 0)

_unknown_direction_logged = False
_window_logged: set[str] = set()


def is_outgoing(row: dict[str, Any]) -> bool:
    """Whether the user sent this parcel; an unreadable row counts as incoming."""
    global _unknown_direction_logged  # noqa: PLW0603
    value = row.get("isSender")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    if not _unknown_direction_logged:
        _unknown_direction_logged = True
        _LOGGER.warning(
            "MyCargus inbox row carries no readable direction (isSender=%r, keys "
            "%s); treating it as incoming. Please open a GitHub issue with this "
            "line",
            type(value).__name__,
            sorted(row),
        )
    return False


def placeholder_trace(row: dict[str, Any]) -> dict[str, Any]:
    """A trace-shaped stand-in for an AWB the trace does not answer (yet)."""
    summary = next(
        (row[key] for key in _SUMMARY_KEYS if isinstance(row.get(key), str)), None
    )
    return {"awbBarcode": awb_of(row), "statusMessage": summary, "events": []}


def _note_window(kind: str, row: dict[str, Any]) -> None:
    """Warn once per kind: the window's reading is an assumption so far."""
    if kind in _window_logged:
        return
    _window_logged.add(kind)
    _LOGGER.warning(
        "MyCargus delivery window %s (arrivingTimeFrom=%r, arrivingTimeTo=%r, "
        "date1=%r, status=%r). Please open a GitHub issue with this line so the "
        "reading can be confirmed",
        kind,
        row.get("arrivingTimeFrom"),
        row.get("arrivingTimeTo"),
        row.get("date1"),
        row.get("status"),
    )


def _clock(value: object) -> time | None:
    """An ``HH:MM[:SS]`` time of day, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def _window_day(row: dict[str, Any], parcel: dict) -> date | None:
    """The day the arrival times refer to.

    ``date1`` when the inbox gives one; otherwise, for a parcel out with the
    courier, the day of its newest scan, since that window is for today.
    """
    explicit = parse_wall_clock(row.get("date1"))
    if explicit is not None:
        return explicit.astimezone(_CLOCK).date()
    if parcel["status"] is not ParcelStatus.OUT_FOR_DELIVERY:
        return None
    history = parcel["raw"]["trace"].get("events") or []
    moments: list[datetime] = [
        moment
        for event in history
        if (moment := parse_wall_clock(event.get("date"))) is not None
    ]
    return max(moments).astimezone(_CLOCK).date() if moments else None


def delivery_window(row: dict[str, Any], parcel: dict) -> tuple[str | None, str | None]:
    """``planned_from``/``planned_to`` from the inbox's arrival times.

    The times carry no date and read ``00:00:00`` when Cargus has no window,
    so a window needs both a non-midnight time and a day to put it on.
    """
    if parcel["delivered"]:
        return None, None
    raw_from, raw_to = row.get("arrivingTimeFrom"), row.get("arrivingTimeTo")
    start, end = _clock(raw_from), _clock(raw_to)
    if (raw_from and start is None) or (raw_to and end is None):
        _note_window("has an unreadable time", row)
        return None, None
    if start in (None, _NO_TIME) and end in (None, _NO_TIME):
        return None, None
    day = _window_day(row, parcel)
    if day is None:
        _note_window("has times but no day to put them on", row)
        return None, None
    _note_window("read for the first time", row)
    planned_from = (
        datetime.combine(day, start, _CLOCK).isoformat()
        if start not in (None, _NO_TIME)
        else None
    )
    planned_to = (
        datetime.combine(day, end, _CLOCK).isoformat()
        if end not in (None, _NO_TIME) and (start is None or end > start)
        else None
    )
    return planned_from, planned_to


def normalize_account_parcel(
    row: dict[str, Any], trace: dict[str, Any], *, include_history: bool
) -> dict:
    """Canonical parcel from the trace, with the inbox row kept in ``raw``."""
    parcel = normalize_parcel(trace, include_history=include_history)
    parcel["raw"] = {"inbox": row, "trace": trace}
    parcel["planned_from"], parcel["planned_to"] = delivery_window(row, parcel)
    return parcel
