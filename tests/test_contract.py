"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.cargus.account.parcels import normalize_account_parcel
from custom_components.cargus.const import ParcelStatus
from custom_components.cargus.parcels import parse_iso
from custom_components.cargus.tracking import status as status_module
from custom_components.cargus.tracking.parcels import normalize_parcel

from .account.fakes import inbox_row, trace
from .payloads import active_sample, delivered_sample, event, transit_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

AWB = "1000000101"
READY = "Ready for collection"


@pytest.fixture
def pickup_wordings(monkeypatch):
    """Map an invented arrival wording, the way a confirmed one would be."""
    monkeypatch.setitem(
        status_module._EVENT_MAP, READY.casefold(), ParcelStatus.AT_PICKUP_POINT
    )


def _collected() -> dict:
    sample = delivered_sample()
    sample["events"] = [
        event(READY, "2026-05-04T09:00:00"),
        event("Delivered", "2026-05-05T12:00:00"),
    ]
    return sample


def _at_point() -> dict:
    sample = active_sample()
    sample["events"] = [event(READY, "2026-05-04T09:00:00")]
    return sample


def _account(window: dict, *events: tuple[str, str]) -> dict:
    row = inbox_row(AWB, date1="2026-10-25T00:00:00", **window)
    return normalize_account_parcel(row, trace(AWB, *events), include_history=False)


def _moment_only() -> dict:
    return _account(
        {"arrivingTimeFrom": "13:00:00", "arrivingTimeTo": "00:00:00"},
        ("2026-10-23T08:15:00", "Consolidation scan"),
    )


def _window() -> dict:
    return _account(
        {"arrivingTimeFrom": "10:00:00", "arrivingTimeTo": "14:00:00"},
        ("2026-10-23T08:15:00", "Consolidation scan"),
    )


def _window_when_done() -> dict:
    return _account(
        {"arrivingTimeFrom": "10:00:00", "arrivingTimeTo": "14:00:00"},
        ("2026-10-25T12:00:00", "Delivered"),
    )


SAMPLES = {
    "delivered": lambda: normalize_parcel(delivered_sample()),
    "active": lambda: normalize_parcel(active_sample()),
    "transit": lambda: normalize_parcel(transit_sample()),
    "bare": lambda: normalize_parcel({"awbBarcode": AWB}),
    "at_point": lambda: normalize_parcel(_at_point()),
    "collected": lambda: normalize_parcel(_collected()),
    "moment_only": _moment_only,
    "window": _window,
    "window_when_done": _window_when_done,
}


@pytest.fixture(params=SAMPLES)
def parcel(request, pickup_wordings) -> dict:
    return SAMPLES[request.param]()


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_pickup_point_is_never_named(parcel):
    assert parcel["pickup_point"] is None


def test_a_parcel_without_an_arrival_event_is_not_pickup():
    assert normalize_parcel(active_sample())["pickup"] is False


def test_a_parcel_at_the_point_is_pickup(pickup_wordings):
    parcel = normalize_parcel(_at_point())
    assert parcel["status"] is ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True


def test_a_collected_pickup_parcel_stays_pickup(pickup_wordings):
    parcel = normalize_parcel(_collected())
    assert parcel["delivered"] is True
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_an_account_parcel_follows_the_same_pickup_rule(pickup_wordings):
    row = inbox_row(AWB)
    reply = trace(
        AWB, ("2026-05-04T09:00:00", READY), ("2026-05-05T12:00:00", "Delivered")
    )
    parcel = normalize_account_parcel(row, reply, include_history=False)
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = _moment_only()
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(parcel["planned_from"]).date()


def test_a_window_keeps_both_ends():
    parcel = _window()
    assert parcel["planned_from"] == "2026-10-25T10:00:00+02:00"
    assert parcel["planned_to"] == "2026-10-25T14:00:00+02:00"


def test_a_delivered_parcel_loses_its_window():
    parcel = _window_when_done()
    assert parcel["delivered"] is True
    assert (parcel["planned_from"], parcel["planned_to"]) == (None, None)
