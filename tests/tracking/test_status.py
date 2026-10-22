"""Tests for the Cargus status vocabulary."""

import logging

import pytest

from custom_components.cargus.const import ParcelStatus
from custom_components.cargus.tracking import status
from custom_components.cargus.tracking.status import (
    map_event_wording,
    map_summary_wording,
    warn_unmapped_status,
)


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        ("Drop off at delivery location by sender", ParcelStatus.IN_TRANSIT),
        ("  pickup   SHIPMENT ", ParcelStatus.IN_TRANSIT),
        ("Received in warehouse Oradea [Depozit]", ParcelStatus.IN_TRANSIT),
        ("Consolidation scan", ParcelStatus.IN_TRANSIT),
        ("Automatic parcel sorting", ParcelStatus.IN_TRANSIT),
        ("With courier", ParcelStatus.OUT_FOR_DELIVERY),
        ("Delivered", ParcelStatus.DELIVERED),
        ("Something new", None),
        ("", None),
        (None, None),
    ],
)
def test_event_wording_map(wording, expected):
    assert map_event_wording(wording) is expected


def test_summary_wording_map():
    assert map_summary_wording("New") is ParcelStatus.REGISTERED
    assert map_summary_wording("In warehouse") is ParcelStatus.IN_TRANSIT
    assert map_summary_wording("Delivered") is ParcelStatus.DELIVERED
    assert map_summary_wording("Else") is None
    assert map_summary_wording(None) is None


def test_unreachable_statuses_stay_unmapped():
    """No wording has been seen for these, so none maps to them."""
    reachable = set(status._EVENT_MAP.values()) | set(status._SUMMARY_MAP.values())
    reachable |= {s for _, s in status._EVENT_PREFIXES}
    assert not reachable & {
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.PROBLEM,
    }


def test_warning_is_one_shot_and_carries_the_issue_link(caplog):
    with caplog.at_level(logging.WARNING):
        warn_unmapped_status("Odd wording")
        warn_unmapped_status("Odd wording")
    assert len(caplog.records) == 1
    assert "template=unrecognised_status.yml" in caplog.text
    assert "Odd wording" in caplog.text


def test_warning_does_not_claim_the_parcel_is_unknown(caplog):
    with caplog.at_level(logging.WARNING):
        warn_unmapped_status("Another wording")
    assert "unknown" not in caplog.text
