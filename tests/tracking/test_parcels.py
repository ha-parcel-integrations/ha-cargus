"""Tests for the Cargus status map and ``normalize_parcel``."""

import logging

from custom_components.cargus.const import (
    CAPABILITIES,
    CAPABILITIES_BY_VARIANT,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    PENDING_CAPABILITIES_BY_VARIANT,
    ParcelStatus,
)
from custom_components.cargus.tracking.parcels import (
    build_history,
    derive_status,
    normalize_parcel,
    parse_wall_clock,
    tracking_url,
)
from tests.payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    event,
    transit_sample,
)

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


def test_normalize_publishes_exactly_the_canonical_keys():
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES
    assert not CAPABILITIES & PENDING_CAPABILITIES
    assert set(CAPABILITIES_BY_VARIANT) == set(PENDING_CAPABILITIES_BY_VARIANT)
    for variant, populated in CAPABILITIES_BY_VARIANT.items():
        pending = PENDING_CAPABILITIES_BY_VARIANT[variant]
        assert populated | pending <= KNOWN_CAPABILITIES
        assert not populated & pending


def test_capabilities_match_what_normalize_parcel_returns():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert parcel["url"] is not None
    assert parcel["history"]
    for absent in (
        "weight",
        "dimensions",
        "planned_from",
        "planned_to",
        "pickup_point",
    ):
        assert parcel[absent] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Cargus"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    # 12:26:46.647 Bucharest wall clock in May is UTC+3.
    assert parcel["delivered_at"] == "2026-05-05T12:26:46.647000+03:00"
    assert parcel["pickup"] is False
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["history"] is None
    assert parcel["url"] == (
        "https://www.cargus.ro/personal/urmareste-coletul/"
        f"?tracking_number={DELIVERED_CODE}"
    )


def test_normalize_out_for_delivery_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["raw_status"] == "With courier"


def test_normalize_in_transit_parcel():
    parcel = normalize_parcel(transit_sample())
    assert parcel["status"] is ParcelStatus.IN_TRANSIT


def test_drop_off_first_history_is_in_transit():
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "statusMessage": "New",
        "events": [
            event("Drop off at delivery location by sender", "2026-05-04T10:00:00.1")
        ],
    }
    parcel = normalize_parcel(raw, include_history=True)
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is False
    assert parcel["history"][0]["status"] is ParcelStatus.IN_TRANSIT


def test_summary_only_new_without_events_is_registered():
    parcel = normalize_parcel(
        {"awbBarcode": ACTIVE_CODE, "statusMessage": "New", "events": []}
    )
    assert parcel["status"] is ParcelStatus.REGISTERED
    assert parcel["raw_status"] == "New"


def test_summary_never_beats_events():
    """The summary keeps ``New`` after scans start."""
    raw = transit_sample()
    raw["statusMessage"] = "New"
    assert derive_status(raw) is ParcelStatus.IN_TRANSIT


def test_placeholder_is_unknown_and_silent(caplog):
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel({"awbBarcode": ACTIVE_CODE})
    assert parcel["status"] is ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None
    assert not caplog.records


def test_unmapped_newest_event_falls_back_to_earlier_event(caplog):
    raw = transit_sample()
    raw["events"].append(event("Attempted delivery", "2026-05-06T09:00:00.0"))
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Attempted delivery"
    assert "Attempted delivery" in caplog.text


def test_unmapped_event_only_falls_back_to_summary(caplog):
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "statusMessage": "In warehouse",
        "events": [event("Attempted delivery", "2026-05-06T09:00:00.0")],
    }
    with caplog.at_level(logging.WARNING):
        assert derive_status(raw) is ParcelStatus.IN_TRANSIT
    assert "Attempted delivery" in caplog.text


def test_unmapped_everything_is_unknown_with_warning(caplog):
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "statusMessage": "Mystery",
        "events": [event("Attempted delivery", "2026-05-06T09:00:00.0")],
    }
    with caplog.at_level(logging.WARNING):
        assert derive_status(raw) is ParcelStatus.UNKNOWN
    assert "Attempted delivery" in caplog.text


def test_unmapped_summary_alone_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert (
            derive_status({"awbBarcode": ACTIVE_CODE, "statusMessage": "Mystery"})
            is ParcelStatus.UNKNOWN
        )
    assert "Mystery" in caplog.text


def test_same_millisecond_events_keep_wire_order_and_sort_by_instant():
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "events": [
            event("With courier", "2026-05-05T10:26:06.08"),
            event("Consolidation scan", "2026-05-05T10:00:00.5"),
            event("Received in warehouse Brasov [Depozit]", "2026-05-05T10:00:00.5"),
        ],
    }
    wordings = [h["raw_status"] for h in build_history(raw)]
    assert wordings == [
        "Consolidation scan",
        "Received in warehouse Brasov [Depozit]",
        "With courier",
    ]


def test_exact_duplicates_collapse():
    first = event("Received in warehouse Zalau [Depozit]", "2026-05-04T16:29:37.283")
    raw = {"awbBarcode": ACTIVE_CODE, "events": [first, dict(first)]}
    assert len(build_history(raw)) == 1


def test_history_is_capped_to_the_newest_events():
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "events": [
            event("Consolidation scan", f"2026-05-04T10:00:{i:02d}.0")
            for i in range(30)
        ],
    }
    history = build_history(raw)
    assert len(history) == 20
    assert history[-1]["timestamp"].startswith("2026-05-04T10:00:29")


def test_history_keeps_unmapped_wording_as_null_status(caplog):
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "events": [event("Attempted delivery", "2026-05-06T09:00:00.0")],
    }
    with caplog.at_level(logging.WARNING):
        history = build_history(raw)
    assert history == [
        {
            "timestamp": "2026-05-06T09:00:00+03:00",
            "status": None,
            "raw_status": "Attempted delivery",
        }
    ]


def test_events_without_a_readable_clock_are_skipped():
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "statusMessage": "In warehouse",
        "events": [
            "garbage",
            event("Delivered", "not-a-date"),
            {"date": None, "event": "Delivered"},
        ],
    }
    assert build_history(raw) == []
    assert derive_status(raw) is ParcelStatus.IN_TRANSIT


def test_event_without_wording_is_ignored_for_status():
    raw = {
        "awbBarcode": ACTIVE_CODE,
        "events": [{"date": "2026-05-06T09:00:00.0", "event": None}],
    }
    assert derive_status(raw) is ParcelStatus.UNKNOWN
    assert build_history(raw)[0]["status"] is None


def test_parse_wall_clock_handles_fractions_offsets_and_garbage():
    assert parse_wall_clock("2026-05-04T16:01:15.1234567").microsecond == 123456
    assert parse_wall_clock("2026-01-04T16:01:15").utcoffset().total_seconds() == 7200
    assert (
        parse_wall_clock("2026-05-04T16:01:15+00:00").utcoffset().total_seconds() == 0
    )
    assert parse_wall_clock("nope") is None
    assert parse_wall_clock(None) is None


def test_tracking_url_needs_a_code():
    assert tracking_url(None) is None


def test_raw_is_the_full_untrimmed_reply():
    raw = delivered_sample()
    assert normalize_parcel(raw)["raw"] is raw
    assert raw["confirmedBy"] == "Jane Doe; PIN:0000"


def test_delivered_by_summary_alone_has_no_delivered_at():
    parcel = normalize_parcel(
        {"awbBarcode": ACTIVE_CODE, "statusMessage": "Delivered", "events": []}
    )
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_summary_delivered_beats_an_older_scan_when_the_last_scan_is_unseen(caplog):
    raw = delivered_sample()
    raw["events"][-1]["event"] = "Handed to neighbour"
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    # no Delivered scan exists, so the newest scan stands in
    assert parcel["delivered_at"] == "2026-05-05T12:26:46.647000+03:00"
    assert parcel["raw_status"] == "Handed to neighbour"
    assert "Handed to neighbour" in caplog.text


def test_newest_mapped_event_still_wins_over_a_delivered_summary():
    raw = active_sample()
    raw["statusMessage"] = "Delivered"
    assert derive_status(raw) is ParcelStatus.OUT_FOR_DELIVERY


def test_in_warehouse_summary_does_not_override_an_older_scan():
    raw = transit_sample()
    raw["events"].append(event("Attempted delivery", "2026-05-06T09:00:00.0"))
    raw["statusMessage"] = "In warehouse"
    assert derive_status(raw) is ParcelStatus.IN_TRANSIT
