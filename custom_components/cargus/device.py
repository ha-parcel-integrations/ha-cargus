"""The device every entity of this integration belongs to.

One place, because sensors and the button must both land on the
*same* device entry — and because the account-based variant only has to change
this file to name devices per account.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import CONF_SOURCE, DOMAIN, SOURCE_ACCOUNT

CONFIGURATION_URL = "https://www.cargus.ro"

ATTRIBUTION = "Data provided by Cargus"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this hub.

    Each account gets its own device, named after the account.
    """
    is_account = entry.data.get(CONF_SOURCE) == SOURCE_ACCOUNT
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title if is_account else "Cargus",
        manufacturer="Cargus",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )
