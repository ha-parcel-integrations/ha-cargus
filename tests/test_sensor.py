"""Tests for Cargus sensor property logic."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.cargus.const import ParcelStatus
from custom_components.cargus.sensor import (
    CargusAwaitingPickupSensor,
    CargusDeliveredParcelsSensor,
    CargusEnRouteToPickupPointSensor,
    CargusIncomingParcelsSensor,
    CargusLastUpdateSensor,
    CargusNextDeliverySensor,
    CargusParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(data: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
) -> dict:
    return {
        "carrier": "Cargus",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = CargusIncomingParcelsSensor(coordinator, _entry(), lambda _: None, set())
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = CargusParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = CargusParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2099-05-02T10:00:00Z"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = CargusNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2099, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_without_moments():
    sensor = CargusNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = CargusNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_skips_a_window_that_has_ended():
    coordinator = _coordinator(
        [
            _parcel("OLD", planned_from="2000-01-01T10:00:00Z"),
            _parcel("NEW", planned_from="2099-01-01T10:00:00Z"),
        ]
    )
    sensor = CargusNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "NEW"


def test_next_delivery_keeps_a_window_still_open():
    parcel = _parcel("OPEN", planned_from="2000-01-01T10:00:00Z")
    parcel["planned_to"] = "2099-01-01T10:00:00Z"
    sensor = CargusNextDeliverySensor(_coordinator([parcel]), _entry())
    assert sensor.extra_state_attributes["barcode"] == "OPEN"


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = CargusDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = CargusLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment


def test_en_route_counts_only_pickup_parcels_not_yet_there():
    parcels = [
        _parcel("A", ParcelStatus.IN_TRANSIT, pickup=True),
        _parcel("B", ParcelStatus.IN_TRANSIT),
        _parcel("C", ParcelStatus.AT_PICKUP_POINT, pickup=True),
        _parcel("D", ParcelStatus.RETURNING, pickup=True),
    ]
    sensor = CargusEnRouteToPickupPointSensor(_coordinator(parcels), _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "A"


def test_awaiting_pickup_counts_only_at_pickup_point():
    parcels = [
        _parcel("A", ParcelStatus.AT_PICKUP_POINT, pickup=True),
        _parcel("B", ParcelStatus.IN_TRANSIT, pickup=True),
        _parcel("C", ParcelStatus.RETURNING, pickup=True),
    ]
    sensor = CargusAwaitingPickupSensor(_coordinator(parcels), _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "A"
    assert CargusAwaitingPickupSensor(_coordinator([]), _entry()).native_value == 0
