# Working in this repository

Home Assistant custom integration for **Cargus** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

API mechanics — endpoints, parameters, status vocabularies — live in the
private `carrier-research/cargus/api/` and are **never** copied here.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_tracking_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. A field you could not verify (no real parcel yet) goes in `PENDING_CAPABILITIES` instead of being left out — the site then says "awaiting data" rather than claiming the API never exposes it; move it into `CAPABILITIES` once confirmed |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **The optional pickup-point summaries are a pair.** A carrier that can tell
  a parcel is destined for a pickup point exposes
  `en_route_to_pickup_point` for `pickup is true` before it arrives; one that
  can reach `ParcelStatus.AT_PICKUP_POINT` also exposes `awaiting_pickup`.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.

## Carrier-specific notes

Two sources, picked from a menu at setup: `tracking/` (a nine- or ten-digit
AWB the user enters) and `account/` (the MyCargus inbox of a signed-in user).
The domain root holds only dispatch and shared HA presentation (`parcels.py`
has the list helpers both sources share).

- **`entry.data[CONF_SOURCE]` picks the source; a missing key is
  `SOURCE_TRACKING`**, because every entry from before the account source is
  a tracking hub. The tracking hub stays a singleton through its old unique
  id (`DOMAIN`); account hubs are multi-entry (`account:<email>`). Do **not**
  put `single_config_entry` back in the manifest.
- **The account reuses the tracking trace for status and history.** The inbox
  only supplies which AWBs belong to the account, their direction
  (`isSender`) and the arrival times; each AWB is then traced exactly like a
  tracking code, so one status map serves both sources.
  `CargusAccountCoordinator` subclasses the tracking coordinator for its event
  firing, refusal repair and tiers. An AWB the trace answers empty for takes
  its status from the inbox row's summary. The account's detail view is only
  probed once per direction and logged as a shape, for a future switch.
- **Sign-in is a scripted B2C login with the stored password**, on its own
  short-lived session (and the refresh too): B2C cookies must never land in
  HA's shared jar (the PPL CZ failure). A still-valid access token is reused,
  an expired one refreshed, a refused refresh falls back to a full sign-in;
  only a rejected password raises `ConfigEntryAuthFailed`. Tokens persist in
  `entry.data`. Session eviction against the app is untested.
- **Two header variants carry the phone number** on signed-in calls; the
  client starts with the web one and switches on a `400`/`403`, remembering
  the winner. The web variant was accepted on the first real account.
- **Delivery window (account only, an assumption).** The inbox's arrival
  times are time-of-day strings, `00:00:00` meaning none. A window is put on
  `date1` when given, otherwise on the newest scan's day for a parcel out with
  the courier, and never on a delivered parcel. Each reading kind logs a
  one-shot WARNING asking for a report; `delivery_window` sits in the
  account's `PENDING_CAPABILITIES_BY_VARIANT` until a real one is seen.
- **Capabilities are per source** (`CAPABILITIES_BY_VARIANT` /
  `PENDING_CAPABILITIES_BY_VARIANT`, `"Tracking"`/`"Account"`, bpost's shape);
  the flat names are aliases of the tracking variant.
- **Account options reload** (`async_schedule_reload`, no update listener);
  the tracking hub keeps its listener and live refresh. Services act on the
  tracking hub only and are registered only by it.
- **PII in the inbox row.** It names sender, receiver and destination. It sits
  in `raw` like the rest of the record, so it travels with the parcel into
  sensor attributes and bus events (raw is never trimmed in this suite);
  only diagnostics replace it with its shape (keys and types), and they
  redact the e-mail, password, phone and tokens from `entry.data`.
- **A partial inbox fails the poll.** Every list type has to answer; one
  failing list would otherwise drop its parcels and announce them as new
  once it is back. Lasting failures warn once per kind, then log at debug.
- **Requests carry per-request headers.** `tracking/headers.py` builds them
  as a pure function of a message; the account's app variant reuses it with
  the phone number as the message.
  Never log the request headers.
- **A refusal is never not-found.** `400`/`401`/`403`, or headers that cannot
  be built, raise `CargusGateError`: the coordinator raises the `request_refused`
  repair, keeps last-good data and never flips a parcel. The repair clears on
  the next answered request. This is an integration failure, not a reauth,
  because no user credential exists.
- **Empty answers are ambiguous.** `204` (or an empty body) means unknown
  *or* expired. A code never found shows an `unknown` placeholder; a code
  found earlier keeps its last-good data and status.
- **Status comes from events, not the summary.** The newest mappable event
  wins; the summary only speaks for a parcel without events (it keeps `New`
  after scans start). The one exception is `Delivered`: when the newest
  event has an unseen wording and the summary says `Delivered`, the parcel
  is delivered rather than falling back to an older `With courier` scan (it
  would otherwise be polled forever); `delivered_at` is then the newest
  scan's time. Any other unmapped wording falls back to the newest mappable
  earlier event, then the summary, then `unknown`, with a one-shot WARNING.
  A failed attempt (`Consignee not home`, `Bad Address`, seen on a real
  account) is `problem`, as Canada Post files its attempted delivery.
  `at_pickup_point` and `returning` are unreachable until a wording is seen.
  A parcel handed to a third party stays on its last warehouse scan; no
  status is invented for long silences.
- **Clock.** Event dates are offset-less wall clocks in `Europe/Bucharest`;
  scans sharing one millisecond keep wire order (stable sort), exact
  duplicates collapse.
- **Fields that are `None` on purpose:** `planned_from`/`planned_to` on a
  tracking hub (no ETA in the reply; an account fills them, see above),
  `weight`, `dimensions`, `sender`, `receiver`, and `pickup_point` (pending a
  real pickup-point parcel, so it sits in `PENDING_CAPABILITIES`). `pickup`
  is always `False`.
- **PII in `raw`.** The reply carries a signer name and PIN and a sender
  reference. `raw` keeps the full reply; they are never surfaced in an entity
  or attribute. Diagnostics redact by key (`awbBarcode`, `confirmedBy`,
  `responseCode`, and the scan places `localityName`/`countyName`, since the
  last scan names the recipient's town) and keep the events and wordings so a
  report stays useful.
- **Outgoing comes from the account only.** The account inbox says which
  side the user is on, so the account entry has the outgoing sensors and
  events; a row without a readable direction is incoming with a one-shot
  warning. The tracking hub has no direction signal and treats every parcel
  as incoming; no sent parcel has been seen on an account yet.
- **Pickup-point sensors.** Only `awaiting_pickup` exists. The
  `en_route_to_pickup_point` view is left out because `pickup` is never true
  before a parcel arrives (no destination is known in advance).
- **Calendar.** The trace reports no expected delivery time, so on a
  tracking hub the calendar, `next_delivery` and `delivery_time_changed` stay
  empty; an account fills them from the inbox's delivery window.
- **Tracking URL** uses the Romanian path; the English page misreports valid
  AWBs.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Parcels` and
`Settings` — or, where the carrier supports outgoing parcels, `Incoming
parcels` / `Outgoing parcels` / `Settings` (see the next section).
`Parcels` is one editable multi-code list; `Settings` is
a flat form — some carriers use one sectioned form
(`data_entry_flow.section`) instead; both are generator variants, not carrier
decisions. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default, and the `--auth byo-key` build) apply
  changes live: an update listener calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately (this is also the resume
  path after polling has fully suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Auth models

Three `--auth` builds, one axis: **what the config flow asks for and
validates**, not how tracking works. `none` and `byo-key` both key tracking on
codes the user types in (`Parcels`/`Settings` options, `track_parcel` /
`untrack_parcel` services, the account-less coordinator and its full-stop /
delivered-skip behaviour below) — `byo-key` only adds a required key field
validated against the carrier's **official** API at setup, `CONF_API_KEY` in
`entry.data`, and a reauth flow for when the key is rotated or revoked outside
Home Assistant (`CargusAuthError` from any per-parcel fetch raises
`ConfigEntryAuthFailed` for the whole poll — one credential covers every
tracked code, so a rejected key is never treated as one parcel's problem).
`credentials` is the only one that changes the *tracking* model too (an
account feed, not user-entered codes) — see the split above.

Reach for `byo-key` only when carrier-research has already established that
the carrier's *unauthenticated/consumer* surface is unusable (bot-walled,
requires a session a script can't hold) and that the *official* developer key
is reachable by a private individual — `key_access: consumer` in the research
doc's front matter, not `business`. A `business`-gated key is a wall, not a
BYO key, whatever the portal's own copy claims (see
`carrier-research/CLAUDE.md`'s "Standing rulings").

## Incoming and outgoing parcels

**Add outgoing support whenever the carrier lets a consumer send a parcel** —
a C2C shipment, a locker drop-off, a marketplace or returns label. It is not
an optional extra to bolt on later: without it a parcel the user sent counts
towards `incoming_active` and sits on their dashboard next to the ones they
are waiting for. A carrier that genuinely has no consumer-sending surface is
exempt — say so in *Carrier-specific notes* so the gap reads as a decision.

Where the direction comes from has exactly two answers, and which one applies
follows from the carrier, not from taste:

- **Account-based** — the account feed distinguishes them, so derive it and
  never ask the user. A shipment matching neither side logs a one-shot
  warning and defaults to incoming rather than disappearing from every list.
  References: `ha-ppl-cz` (one call, split on a field), `ha-dhl-nl` (a
  separate "sent" endpoint).
- **Account-less** — the payload cannot reveal it (the user's own parcel and
  a stranger's look alike, and there is no account identity to compare a
  party against), so the **user declares it per parcel**: two menu entries in
  the options flow, each the same multi-code list, plus a `direction` field on
  `track_parcel`. Store it as `CONF_DIRECTION` on the `CONF_PARCELS` dicts,
  defaulting to incoming, so entries written before the option existed need no
  migration. Re-filing a code under the other direction moves it instead of
  erroring — that is the correction path. Reference: `ha-packeta`.
  **Never infer direction from free-text event wording**: a handover sentence
  that happens to name the drop-off point is not a structured field, says
  nothing before handover, and silently ties the split to one locale.

Above that split the shape is identical either way, and is suite-wide:
`coordinator.outgoing` / `coordinator.delivered_outgoing` alongside `data` /
`delivered`; `outgoing_parcels` + `outgoing_delivered_parcels` summary
sensors (their unique_ids belong in `non_parcel_unique_ids`); per-parcel
sensors spawned for **both** directions; the
`<domain>_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
event pair, with **no** `registered` and no delivery-time event for outgoing;
`awaiting_pickup`, `next_delivery` and the calendar staying incoming-only;
and both new lists in `diagnostics.py`. The aggregator needs no change — it
buckets on the sensor suffix and the event prefix. Also set
`directions: incoming+outgoing` for the carrier in the docs site's
`data/carriers.yml`.

## Tracking-code validation

`valid_tracking_code` in `config_flow.py` requires nine or ten digits after
whitespace is stripped, and rejects anything else before a request is made.
This is a deliberate local divergence from the suite's accept-any-code
convention: the shape is a plain numeric AWB, and a wrong number would cost a
request that can only come back empty. The 2014-2016 letter shape (three
letters, eight digits, a letter) is not accepted.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `tracking/coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` recompute `update_interval` at the end of every
refresh. The reference carrier in `ha-carrier-template` (its `coordinator.py`)
is the canonical implementation every carrier mirrors; the design rationale (quiet window, tiers, stagger,
backoff, delivered-skip) is spelled out below.

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `tracking/api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.
- **Delivered codes are skipped from the fetch (account-less carriers only):**
  once a tracking code's payload comes back `delivered`, `coordinator.py`
  excludes it from the next cycle's fetch — its payload can never change
  again. `self._delivered_codes` (keyed on the tracking code, not the barcode)
  is rebuilt from each cycle's results and intersected with the tracked set on
  untrack. The code stays in the options list, keeps its sensor and its
  cached payload, and still shows under the retention window — it just costs
  no more requests. `coordinator.delivered_codes` surfaces the count in
  diagnostics. Account-based carriers have nothing to skip here — one account
  call already returns everything, so they either have no `delivered_codes` or
  it is always empty.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `tracking/api.py`, `tracking/headers.py` (HTTP client, error types, request headers) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `tracking/status.py`, `tracking/parcels.py` (status map, `normalize_parcel`, history — pure, no I/O) | **yes** |
| `parcels.py` (sort, delivered filter, `parse_iso`) | no |
| `tracking/coordinator.py` (fetch, cache, event firing, repair) | mostly not |
| `account/auth.py`, `account/client.py`, `account/headers.py` (sign-in, token lifecycle, inbox calls, header variants) | **yes** |
| `account/parcels.py`, `account/coordinator.py` (direction, delivery window, inbox polling over the tracking trace) | **yes** |
| `config_flow.py` | partly (code validation; key/credential validation on `--auth byo-key`/`credentials`) |
| `sensor.py` / `button.py` / `device_trigger.py` | no |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`tracking/parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.cargus
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
