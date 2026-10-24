"""Coordinator for the MyCargus account source.

Reads the inbox for the account's AWBs and their direction, then traces each
one through the tracking client. It reuses the tracking coordinator's event
firing, refusal repair and polling tiers; what differs is where the AWBs come
from, the direction split, and that an account never stops polling.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, MID_INTERVAL_MINUTES, ParcelStatus
from ..parcels import apply_delivered_filter, sort_parcels_by_ts
from ..tracking.api import CargusApiClient, CargusApiError, CargusGateError
from ..tracking.coordinator import (
    CargusCoordinator,
    _hottest_tier_minutes,
    _next_update_interval,
)
from .client import (
    CargusAccountAuthError,
    CargusAccountClient,
    CargusAccountError,
    awb_of,
    describe_shape,
)
from .parcels import is_outgoing, normalize_account_parcel, placeholder_trace

_LOGGER = logging.getLogger(__name__)

# Traces run side by side, but an inbox can list many AWBs.
MAX_PARALLEL_TRACES = 4


class CargusAccountCoordinator(CargusCoordinator):
    """Polls one MyCargus inbox and publishes incoming and outgoing parcels."""

    def __init__(
        self,
        hass: HomeAssistant,
        tracking_client: CargusApiClient,
        account_client: CargusAccountClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(hass, tracking_client, entry)
        self.account_client = account_client
        self.outgoing: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        self._full_awb_probed: set[bool] = set()
        self.inbox_size = 0

    async def _async_update_data(self) -> list[dict]:
        try:
            return await self._async_poll()
        except Exception:
            # The base class keeps the last interval after a failure, which
            # after an overnight poll is the whole quiet window.
            now = dt_util.now()
            self.update_interval = _next_update_interval(
                now,
                _hottest_tier_minutes((self.data or []) + self.outgoing, now)
                or MID_INTERVAL_MINUTES,
                self.config_entry.entry_id,
            )
            raise

    async def _async_poll(self) -> list[dict]:
        """Read the inbox, trace each AWB, split by direction and publish."""
        try:
            rows = await self.account_client.async_get_inbox()
        except CargusAccountAuthError as err:
            raise ConfigEntryAuthFailed("MyCargus rejected the password") from err
        except CargusAccountError as err:
            raise UpdateFailed(f"MyCargus inbox unavailable: {err}") from err
        self.inbox_size = len(rows)
        _LOGGER.debug(
            "MyCargus inbox: %s AWBs (%s sent), token from %s",
            len(rows),
            sum(1 for row in rows if is_outgoing(row)),
            self.account_client.last_token_source,
        )
        await self._probe_full_awb(rows)

        awbs = [awb_of(row) or "" for row in rows]
        known = set(awbs)
        self._raw_cache = {k: v for k, v in self._raw_cache.items() if k in known}
        self._delivered_codes &= known
        traces = await self._async_traces(rows, awbs)

        include_history = self._include_history
        incoming: list[dict] = []
        outgoing: list[dict] = []
        for row, trace in zip(rows, traces, strict=True):
            parcel = normalize_account_parcel(
                row, trace, include_history=include_history
            )
            (outgoing if is_outgoing(row) else incoming).append(parcel)

        self._delivered_codes = {
            parcel["barcode"]
            for parcel in incoming + outgoing
            if parcel["delivered"] and parcel["barcode"] in self._raw_cache
        }
        self.delivered = apply_delivered_filter(
            sort_parcels_by_ts(
                [p for p in incoming if p["delivered"]], "delivered_at", descending=True
            ),
            self.config_entry,
        )
        active = sort_parcels_by_ts(
            [p for p in incoming if not p["delivered"]], "planned_from"
        )
        self.outgoing = sort_parcels_by_ts(
            [p for p in outgoing if not p["delivered"]], "planned_from"
        )
        self.delivered_outgoing = apply_delivered_filter(
            sort_parcels_by_ts(
                [p for p in outgoing if p["delivered"]], "delivered_at", descending=True
            ),
            self.config_entry,
        )

        combined_in = active + self.delivered
        self._fire_change_events(combined_in)
        self._known_state = _status_map(combined_in)
        self._known_delivery_times = {
            p["barcode"]: (p.get("planned_from"), p.get("planned_to"))
            for p in combined_in
            if p.get("barcode")
        }
        combined_out = self.outgoing + self.delivered_outgoing
        self._fire_outgoing_change_events(combined_out)
        self._known_outgoing_state = _status_map(combined_out)

        self.last_success_time = datetime.now(UTC)
        now = dt_util.now()
        self._current_tier_minutes = (
            _hottest_tier_minutes(active + self.outgoing, now) or MID_INTERVAL_MINUTES
        )
        self.update_interval = _next_update_interval(
            now, self._current_tier_minutes, self.config_entry.entry_id
        )
        return active

    async def _async_traces(
        self, rows: list[dict[str, Any]], awbs: list[str]
    ) -> list[dict[str, Any]]:
        """Trace every AWB not already known delivered; fall back per AWB."""
        semaphore = asyncio.Semaphore(MAX_PARALLEL_TRACES)

        async def trace(awb: str) -> dict[str, Any] | None:
            async with semaphore:
                return await self._client.async_get_parcel(awb)

        to_fetch = [
            awb
            for awb in dict.fromkeys(awbs)
            if awb and awb not in self._delivered_codes
        ]
        results = dict(
            zip(
                to_fetch,
                await asyncio.gather(
                    *(trace(awb) for awb in to_fetch), return_exceptions=True
                ),
                strict=True,
            )
        )
        refused = answered = False
        empty = 0
        traces: list[dict[str, Any]] = []
        for row, awb in zip(rows, awbs, strict=True):
            result = results.get(awb)
            if isinstance(result, BaseException):
                if not isinstance(result, (CargusApiError, aiohttp.ClientError)):
                    raise result
                refused |= isinstance(result, CargusGateError)
                self._log_fetch_failure(awb, result)
            elif result is not None:
                answered = True
                self._raw_cache[awb] = result
            elif awb in results:
                answered = True
                empty += 1
            traces.append(self._raw_cache.get(awb) or placeholder_trace(row))
        if empty:
            _LOGGER.debug(
                "MyCargus: %s of %s inbox AWBs have no trace (new or expired); "
                "their status comes from the inbox row",
                empty,
                len(to_fetch),
            )
        if answered:
            self._failures_logged.clear()
        self._sync_refusal_issue(refused, answered)
        return traces

    async def _probe_full_awb(self, rows: list[dict[str, Any]]) -> None:
        """Log the detail view's shape once per direction, for future mapping."""
        for row in rows:
            for_sender = is_outgoing(row)
            awb = awb_of(row)
            if for_sender in self._full_awb_probed or awb is None:
                continue
            self._full_awb_probed.add(for_sender)
            try:
                payload = await self.account_client.async_get_full_awb(
                    awb, for_sender=for_sender
                )
            except CargusAccountError as err:
                _LOGGER.debug("MyCargus FullAwb probe failed: %s", err)
                continue
            _LOGGER.debug(
                "MyCargus FullAwb shape (forSender=%s): %s",
                for_sender,
                describe_shape(payload),
            )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire outgoing status-changed / delivered events.

        No ``registered`` and no delivery-time event for outgoing; the hop to
        ``delivered`` fires only ``_outgoing_parcel_delivered``.
        """
        if self._known_outgoing_state is None:
            return
        device_id = self._device_id()
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            old_status = self._known_outgoing_state[barcode]
            new_status = parcel["status"]
            if new_status == old_status:
                continue
            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    },
                )


def _status_map(parcels: list[dict]) -> dict[str, ParcelStatus]:
    return {p["barcode"]: p["status"] for p in parcels if p.get("barcode")}
