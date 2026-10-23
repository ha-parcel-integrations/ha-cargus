"""Tests for the account request headers and the inbox-to-parcel helpers."""

import logging

import pytest

from custom_components.cargus.account import parcels as account_parcels
from custom_components.cargus.account.headers import (
    VARIANT_APP,
    VARIANT_WEB,
    build_account_headers,
    normalize_phone,
    phone_key,
)
from custom_components.cargus.account.parcels import (
    delivery_window,
    is_outgoing,
    normalize_account_parcel,
    placeholder_trace,
)
from custom_components.cargus.const import ParcelStatus

from .fakes import inbox_row, trace

AWB = "1000000101"


@pytest.fixture(autouse=True)
def reset_one_shots(monkeypatch):
    monkeypatch.setattr(account_parcels, "_unknown_direction_logged", False)
    monkeypatch.setattr(account_parcels, "_window_logged", set())


def test_normalize_phone_keeps_digits_and_a_leading_plus():
    assert normalize_phone(" 07 12-345 678 ") == "0712345678"
    assert normalize_phone("+40 712 345 678") == "+40712345678"
    assert normalize_phone("") == ""


def test_phone_key_hides_the_number_and_changes_per_call():
    first, second = phone_key("0712345678"), phone_key("0712345678")
    assert first != second
    assert "0712345678" not in first
    assert len(phone_key("+40712345678")) == len(first)


def test_web_headers():
    headers = build_account_headers(VARIANT_WEB, "0712345678", "tok")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Source"] == "1"
    assert "K" in headers
    assert "seed" not in headers


def test_app_headers():
    headers = build_account_headers(VARIANT_APP, "0712345678", "tok")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["s"] == "M"
    assert {"k", "seed"} <= set(headers)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("False", False)],
)
def test_is_outgoing_reads_is_sender(value, expected):
    assert is_outgoing(inbox_row(AWB, isSender=value)) is expected


def test_unreadable_direction_is_incoming_with_one_warning(caplog):
    assert is_outgoing(inbox_row(AWB, isSender=None)) is False
    assert is_outgoing({"awbBarCode": AWB}) is False
    assert caplog.text.count("no readable direction") == 1


def test_placeholder_takes_the_inbox_status():
    assert placeholder_trace(inbox_row(AWB, status="New")) == {
        "awbBarcode": AWB,
        "statusMessage": "New",
        "events": [],
    }
    assert placeholder_trace({"awb": AWB})["statusMessage"] is None


def test_account_parcel_keeps_both_records_in_raw():
    row = inbox_row(AWB)
    reply = trace(AWB, ("2026-10-20T10:00:00", "Consolidation scan"))
    parcel = normalize_account_parcel(row, reply, include_history=True)
    assert parcel["barcode"] == AWB
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["raw"] == {"inbox": row, "trace": reply}
    assert parcel["history"][0]["raw_status"] == "Consolidation scan"
    assert parcel["planned_from"] is None


def _parcel(row, *events, summary="In warehouse"):
    return normalize_account_parcel(
        row, trace(AWB, *events, summary=summary), include_history=False
    )


def test_window_on_the_day_out_with_the_courier(caplog):
    row = inbox_row(AWB, arrivingTimeFrom="10:00:00", arrivingTimeTo="14:00:00")
    parcel = _parcel(
        row,
        ("2026-10-22T18:00:00", "Consolidation scan"),
        ("2026-10-23T08:15:00", "With courier"),
    )
    assert parcel["planned_from"] == "2026-10-23T10:00:00+03:00"
    assert parcel["planned_to"] == "2026-10-23T14:00:00+03:00"
    assert "read for the first time" in caplog.text


def test_window_uses_date1_when_given():
    row = inbox_row(
        AWB,
        date1="2026-10-25T00:00:00",
        arrivingTimeFrom="09:00",
        arrivingTimeTo="00:00:00",
    )
    parcel = _parcel(row, ("2026-10-23T08:15:00", "Consolidation scan"))
    assert parcel["planned_from"] == "2026-10-25T09:00:00+02:00"
    assert parcel["planned_to"] is None


def test_window_end_before_start_is_dropped():
    row = inbox_row(
        AWB,
        date1="2026-10-25T00:00:00",
        arrivingTimeFrom="15:00",
        arrivingTimeTo="09:00",
    )
    parcel = _parcel(row)
    assert parcel["planned_from"] == "2026-10-25T15:00:00+02:00"
    assert parcel["planned_to"] is None


def test_midnight_times_mean_no_window():
    parcel = _parcel(inbox_row(AWB), ("2026-10-23T08:15:00", "With courier"))
    assert (parcel["planned_from"], parcel["planned_to"]) == (None, None)


def test_delivered_parcel_has_no_window():
    row = inbox_row(AWB, arrivingTimeFrom="10:00", arrivingTimeTo="12:00")
    parcel = _parcel(row, ("2026-10-23T12:00:00", "Delivered"))
    assert delivery_window(row, parcel) == (None, None)


def test_times_without_a_day_are_dropped_with_a_warning(caplog):
    row = inbox_row(AWB, arrivingTimeFrom="10:00", arrivingTimeTo="12:00")
    parcel = _parcel(row, ("2026-10-23T08:15:00", "Consolidation scan"))
    assert parcel["planned_from"] is None
    assert "no day to put them on" in caplog.text


def test_out_for_delivery_without_dated_scans_has_no_day():
    row = inbox_row(AWB, arrivingTimeFrom="10:00", arrivingTimeTo="12:00")
    parcel = _parcel(row, ("2026-10-23T08:15:00", "With courier"))
    parcel["raw"]["trace"]["events"] = [{"event": "With courier"}]
    assert delivery_window(row, parcel) == (None, None)


def test_unreadable_time_is_dropped_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    row = inbox_row(AWB, arrivingTimeFrom="tomorrow", arrivingTimeTo="12:00")
    parcel = _parcel(row, ("2026-10-23T08:15:00", "With courier"))
    assert parcel["planned_from"] is None
    assert "unreadable time" in caplog.text
