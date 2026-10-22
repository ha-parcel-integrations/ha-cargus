"""Canonical parcel shape for the Cargus tracking reply (pure, no I/O)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..const import HISTORY_MAX_EVENTS, TRACKING_URL, ParcelStatus
from .status import map_event_wording, map_summary_wording, warn_unmapped_status

_CLOCK = ZoneInfo("Europe/Bucharest")


def parse_wall_clock(value: object) -> datetime | None:
    """Parse an offset-less Bucharest wall clock to an aware instant."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=_CLOCK)


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _ordered_events(raw: dict) -> list[tuple[datetime, dict]]:
    """Return the events with a readable clock, oldest first, duplicates collapsed.

    The sort is stable: scans that share one millisecond keep their wire order.
    """
    seen: set[tuple] = set()
    events: list[tuple[datetime, dict]] = []
    for event in raw.get("events") or []:
        if not isinstance(event, dict):
            continue
        moment = parse_wall_clock(event.get("date"))
        if moment is None:
            continue
        key = (
            event.get("date"),
            event.get("event"),
            event.get("countyName"),
            event.get("localityName"),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append((moment, event))
    events.sort(key=lambda item: item[0])
    return events


def derive_status(raw: dict) -> ParcelStatus:
    """Current status from the newest mappable event.

    When that event is not the newest one, a summary of ``Delivered`` still
    wins over an older scan: otherwise a delivered parcel whose final scan has
    an unseen wording would sit on ``out_for_delivery`` and be polled forever.
    The summary otherwise only speaks for a parcel no event maps for.
    """
    summary = raw.get("statusMessage")
    summary_status = map_summary_wording(summary)
    skipped: list[str] = []
    for _, event in reversed(_ordered_events(raw)):
        status = map_event_wording(event.get("event"))
        if status is not None:
            for wording in skipped:
                warn_unmapped_status(wording)
            if skipped and summary_status is ParcelStatus.DELIVERED:
                return ParcelStatus.DELIVERED
            return status
        if event.get("event"):
            skipped.append(str(event["event"]))
    for wording in skipped:
        warn_unmapped_status(wording)
    if summary_status is not None:
        return summary_status
    if summary and not skipped:
        warn_unmapped_status(str(summary))
    return ParcelStatus.UNKNOWN


def build_history(raw: dict, *, max_events: int = HISTORY_MAX_EVENTS) -> list[dict]:
    """Build the canonical ``history`` list, oldest to newest, newest N kept."""
    history = []
    for moment, event in _ordered_events(raw):
        status = map_event_wording(event.get("event"))
        if status is None and event.get("event"):
            warn_unmapped_status(str(event["event"]))
        history.append(
            {
                "timestamp": moment.isoformat(),
                "status": status,
                "raw_status": event.get("event"),
            }
        )
    return history[-max_events:]


def _delivered_at(raw: dict) -> str | None:
    """Time of the newest ``Delivered`` scan, else of the newest scan at all.

    The fallback covers a delivered summary whose final scan has an unseen
    wording; it is the best moment available.
    """
    events = _ordered_events(raw)
    for moment, event in reversed(events):
        if map_event_wording(event.get("event")) is ParcelStatus.DELIVERED:
            return moment.isoformat()
    return events[-1][0].isoformat() if events else None


def _raw_status(raw: dict) -> str | None:
    events = _ordered_events(raw)
    if events:
        return events[-1][1].get("event") or None
    return raw.get("statusMessage") or None


def normalize_parcel(raw: dict[str, Any], *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the reply under ``raw``."""
    tracking_code = raw.get("awbBarcode")
    status = derive_status(raw)
    delivered = status is ParcelStatus.DELIVERED
    return {
        "carrier": "Cargus",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _raw_status(raw),
        "delivered": delivered,
        "delivered_at": _delivered_at(raw) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(raw) if include_history else None,
        "raw": raw,
    }
