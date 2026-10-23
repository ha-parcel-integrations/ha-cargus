"""Constants for the Cargus parcel tracker integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "cargus"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"  # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"  # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"  # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"  # Ready to collect at a pickup location
    DELIVERED = "delivered"  # Handed over
    RETURNING = "returning"  # Failed delivery, going back to sender
    PROBLEM = "problem"  # Carrier reports an exception/issue
    UNKNOWN = "unknown"  # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Every value not listed here must come back as a literal ``None`` from
# normalize_parcel() in tracking/parcels.py (or, for "history", CONF_INCLUDE_HISTORY
# must not be wired to anything real) — never omit the key, just leave it
# empty. The docs site's carrier comparison table is generated straight from
# this constant, so drift here does not stay a local mistake, it becomes a
# wrong claim on the website.
#
#   "weight"           parcel["weight"] is ever non-null
#   "dimensions"        parcel["dimensions"] is ever non-null
#   "delivery_window"   parcel["planned_from"] and/or ["planned_to"] is ever non-null
#   "pickup_point"       parcel["pickup_point"] is ever non-null (beyond the "pickup" bool)
#   "url"                parcel["url"] is ever non-null
#   "history"            the include_history option is implemented and does something
CAPABILITIES_BY_VARIANT = {
    "Tracking": frozenset({"url", "history"}),
    "Account": frozenset({"url", "history"}),
}
CAPABILITIES = CAPABILITIES_BY_VARIANT["Tracking"]

# Fields whose support is not confirmed yet — typically a carrier built without
# a real parcel to check against. A field is in exactly one of three states: in
# CAPABILITIES_BY_VARIANT (seen populated), here (docs site shows "awaiting
# data"), or in neither (the API never exposes it). The account's delivery
# window is read from the inbox's arrival times, so far only seen empty on
# delivered parcels.
PENDING_CAPABILITIES_BY_VARIANT = {
    "Tracking": frozenset({"pickup_point"}),
    "Account": frozenset({"pickup_point", "delivery_window"}),
}
PENDING_CAPABILITIES = PENDING_CAPABILITIES_BY_VARIANT["Tracking"]

# ``TRACKING_API_URL`` is polled with fresh request headers each time;
# ``TRACKING_URL`` is the human-facing deep link surfaced on each parcel's
# ``url`` field (the Romanian path: the English page misreports valid AWBs).
TRACKING_API_URL = (
    "https://cmacwabackend.azurewebsites.net/api/Awbs/ShortTrace"
    "?BarCode={tracking_code}&lang=EN"
)
TRACKING_URL = (
    "https://www.cargus.ro/personal/urmareste-coletul/?tracking_number={tracking_code}"
)

# An entry is either a tracking hub (codes the user enters) or a MyCargus
# account (parcels discovered from the account inbox). Entries created before
# the account source existed carry no source and are tracking hubs.
CONF_SOURCE = "source"
SOURCE_TRACKING = "tracking"
SOURCE_ACCOUNT = "account"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_PHONE = "phone"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight (registered, in_transit, at_pickup_point, unknown, problem,
# returning).
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
