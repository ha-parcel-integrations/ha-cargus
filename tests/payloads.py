"""Sample Cargus tracking replies shared by the test modules.

Modelled on real replies with the AWB, signer and sender reference replaced by
synthetic values. Keep them in one module so a shape change has one place to fix.
"""

from __future__ import annotations

ACTIVE_CODE = "1000000999"
DELIVERED_CODE = "1000000123"


def event(
    wording: str,
    date: str,
    county: str = "Brasov",
    locality: str = "BRASOV",
) -> dict:
    """One entry of the reply's event list."""
    return {
        "date": date,
        "event": wording,
        "countyName": county,
        "localityName": locality,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel: pickup, sorting, warehouse scans, courier, delivered."""
    return {
        "awbBarcode": code,
        "statusMessage": "Delivered",
        "statusColor": "#F28230",
        "undeliveredPieces": "",
        "envelope": "0",
        "parcel": "1",
        "confirmedDate": None,
        "confirmedBy": "Jane Doe; PIN:0000",
        "responseCode": "Ramburs/Retur/Redirectionare: ref-0001",
        "transitTime": None,
        "events": [
            event("Pickup shipment", "2026-05-04T16:01:15.813", "Salaj", "ZALAU"),
            event("Consolidation scan", "2026-05-04T16:29:37.283", "Salaj", "ZALAU"),
            event(
                "Received in warehouse Zalau [Depozit]",
                "2026-05-04T16:29:37.283",
                "Salaj",
                "ZALAU",
            ),
            event(
                "Received in warehouse Resita [Depozit]",
                "2026-05-05T10:25:48.133",
                "Caras-Severin",
                "RESITA",
            ),
            event("With courier", "2026-05-05T10:26:06.08", "Caras-Severin", "RESITA"),
            event("Delivered", "2026-05-05T12:26:46.647", "Caras-Severin", "RESITA"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel (the courier scan is the newest event)."""
    sample = delivered_sample(code)
    sample.update(
        {
            "statusMessage": "In warehouse",
            "confirmedBy": None,
            "responseCode": None,
            "events": sample["events"][:-1],
        }
    )
    return sample


def transit_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel that only reached a warehouse."""
    sample = active_sample(code)
    sample["events"] = sample["events"][:4]
    return sample
