"""Status vocabulary of the Cargus tracking reply."""

from __future__ import annotations

import logging

from ..const import ParcelStatus

_LOGGER = logging.getLogger(__name__)

NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-cargus/issues/new"
    "?template=unrecognised_status.yml"
)

# Keys are case-folded, whitespace-collapsed event wordings. The wire carries
# no codes, only English text (the request pins lang=EN).
_EVENT_MAP: dict[str, ParcelStatus] = {
    "drop off at delivery location by sender": ParcelStatus.IN_TRANSIT,
    "pickup shipment": ParcelStatus.IN_TRANSIT,
    "consolidation scan": ParcelStatus.IN_TRANSIT,
    "automatic parcel sorting": ParcelStatus.IN_TRANSIT,
    "with courier": ParcelStatus.OUT_FOR_DELIVERY,
    "delivered": ParcelStatus.DELIVERED,
}

# The town in "Received in warehouse <Town> [Depozit]" varies.
_EVENT_PREFIXES: tuple[tuple[str, ParcelStatus], ...] = (
    ("received in warehouse ", ParcelStatus.IN_TRANSIT),
)

# Summary wordings, consulted only for a parcel without events.
_SUMMARY_MAP: dict[str, ParcelStatus] = {
    "new": ParcelStatus.REGISTERED,
    "in warehouse": ParcelStatus.IN_TRANSIT,
    "delivered": ParcelStatus.DELIVERED,
}

_unmapped_statuses_logged: set[str] = set()


def _fold(wording: object) -> str:
    return " ".join(str(wording or "").split()).casefold()


def warn_unmapped_status(wording: str) -> None:
    """Log an unmapped wording once per session, with a copy-paste issue link.

    The status shown for the parcel falls back to an earlier scan or the summary
    when one maps, so the text does not claim it is reported as unknown.
    """
    if wording in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(wording)
    _LOGGER.warning(
        "Unrecognised Cargus status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s",
        NEW_ISSUE_URL,
        wording,
    )


def map_event_wording(wording: object) -> ParcelStatus | None:
    """Map one event wording to a status, or ``None`` when it is not mapped."""
    folded = _fold(wording)
    if not folded:
        return None
    mapped = _EVENT_MAP.get(folded)
    if mapped is not None:
        return mapped
    for prefix, status in _EVENT_PREFIXES:
        if folded.startswith(prefix):
            return status
    return None


def map_summary_wording(wording: object) -> ParcelStatus | None:
    """Map the reply's summary wording to a status, or ``None``."""
    return _SUMMARY_MAP.get(_fold(wording))
