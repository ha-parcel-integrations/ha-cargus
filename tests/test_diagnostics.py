"""Tests for Cargus diagnostics."""

from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.cargus.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "1012345600"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "1012345600",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "raw": {
                "awbBarcode": "1012345600",
                "confirmedBy": "Jane Doe; PIN:0000",
                "responseCode": "ref-0001",
                "events": [
                    {
                        "date": "2026-05-05T12:26:46.647",
                        "event": "Delivered",
                        "countyName": "Caras-Severin",
                        "localityName": "RESITA",
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    raw = result["incoming"][0]["raw"]
    assert raw["awbBarcode"] == "**REDACTED**"
    assert raw["confirmedBy"] == "**REDACTED**"
    assert raw["responseCode"] == "**REDACTED**"
    assert raw["events"][0]["localityName"] == "**REDACTED**"
    assert raw["events"][0]["countyName"] == "**REDACTED**"
    # the scan history itself is what makes a report useful
    assert raw["events"][0]["event"] == "Delivered"
    assert raw["events"][0]["date"] == "2026-05-05T12:26:46.647"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }


def test_payload_fields_are_redacted_by_name():
    assert {"awbBarcode", "confirmedBy", "responseCode"} <= TO_REDACT
    assert "raw" not in TO_REDACT
