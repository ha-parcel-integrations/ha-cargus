"""Diagnostics support for the Cargus parcel tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CargusConfigEntry
from .account.client import describe_shape
from .account.coordinator import CargusAccountCoordinator

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # carrier payload fields
    "awbBarcode",
    "confirmedBy",
    "responseCode",
    # the last scan of a delivered parcel names the recipient's town
    "countyName",
    "localityName",
    "trackingNumber",
    "recipient",
    "deliveryAddress",
    "address",
    "postalCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
    "driver",
    "signature",
    "phone",
    "password",
    "access_token",
    "refresh_token",
}


def _shape_inbox_rows(parcels: list[dict]) -> list[dict]:
    """Swap each inbox row for its shape.

    Its keys are not known well enough to redact by name, and the shape is
    what a report needs.
    """
    shaped = []
    for parcel in parcels:
        raw = parcel.get("raw")
        if isinstance(raw, dict) and "inbox" in raw:
            parcel = {**parcel, "raw": {**raw, "inbox": describe_shape(raw["inbox"])}}
        shaped.append(parcel)
    return shaped


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CargusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Cargus config entry."""
    coordinator = entry.runtime_data.coordinator

    diagnostics: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(
            _shape_inbox_rows(coordinator.data or []), TO_REDACT
        ),
        "delivered": async_redact_data(
            _shape_inbox_rows(coordinator.delivered or []), TO_REDACT
        ),
    }
    if isinstance(coordinator, CargusAccountCoordinator):
        client = coordinator.account_client
        diagnostics["counts"]["inbox"] = coordinator.inbox_size
        diagnostics["counts"]["outgoing_active"] = len(coordinator.outgoing)
        diagnostics["counts"]["outgoing_delivered"] = len(
            coordinator.delivered_outgoing
        )
        diagnostics["account"] = {
            "header_variant": client.header_variant,
            "last_token_source": client.last_token_source,
            "shapes": client.shapes,
        }
        diagnostics["outgoing"] = async_redact_data(
            _shape_inbox_rows(coordinator.outgoing), TO_REDACT
        )
        diagnostics["outgoing_delivered"] = async_redact_data(
            _shape_inbox_rows(coordinator.delivered_outgoing), TO_REDACT
        )
    return diagnostics
