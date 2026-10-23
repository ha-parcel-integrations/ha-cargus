# Cargus Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-cargus.svg)](https://github.com/ha-parcel-integrations/ha-cargus/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-cargus/total.svg)](https://github.com/ha-parcel-integrations/ha-cargus/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Cargus](https://www.cargus.ro) parcels in Romania. Two ways to set it up:

- **MyCargus account** — sign in once and the parcels on your account are found for you, the ones you send included.
- **Tracking codes** — no account: you enter the AWB (the nine- or ten-digit tracking number) yourself.

> **Known gaps.** No pickup-point or return wording has been seen yet; a parcel that reports one logs a warning with a link to report it and may show an earlier status or `unknown`. The MyCargus delivery window is read from the account's arrival times, which have only been seen empty so far; the first one logs a warning asking you to report it. The integration uses unofficial routes that can change, so it can stop working until an update ships.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Sign in with your MyCargus account and have every parcel on it found automatically, incoming and sent
- Or track any number of Cargus parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / `problem`), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels, and with an account the parcels you sent
- Read-only **Deliveries** calendar for expected delivery windows (with an account, when Cargus gives one)
- `cargus.track_parcel` / `cargus.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- Either a MyCargus account (e-mail, password and the phone number on the
  account), or a Cargus parcel and its AWB, a nine- or ten-digit number (from
  the shipping confirmation or the sender)

## Installation

### HACS (recommended)

Click the button below to open this repository in HACS, then install **Cargus**:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-cargus&category=integration)

Or add it by hand: in HACS, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-cargus` as an **Integration** and install **Cargus**.

Restart Home Assistant if HACS asks you to.

### Manual

Copy `custom_components/cargus` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cargus)

Add the integration via **Settings → Devices & Services → Add Integration → Cargus** and pick one:

- **MyCargus account** — enter the e-mail address and password you use for MyCargus, and the phone number on that account (for example `07xxxxxxxx`). The password is stored so the integration can sign in again on its own when its session ends; you are only asked again if MyCargus stops accepting it. You can add more than one account.
- **Tracking codes** — there is nothing to fill in: the hub is created immediately.

With tracking codes, add parcels via the integration's **Configure** dialog, the [`cargus.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The AWB is on your shipping confirmation or comes from the sender. Spaces are ignored; anything that is not nine or ten digits is rejected.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Tracking codes only: manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** (tracking codes only) — once every tracked parcel has been
  delivered, or nothing is tracked at all, polling stops until you add a
  parcel back (adding one always triggers an immediate check, regardless of
  the pause). An account keeps polling on the normal cadence, since that is
  how new parcels on it are found.
- A small, fixed per-hub offset is added on top, so not every Cargus
  hub out there polls at exactly the same second.

Without an account Cargus reports no expected delivery window, so a parcel that is out for delivery is always polled on the hot cadence until it is delivered.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Cargus → ⋮ → Delete**. Nothing is stored on Cargus's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.cargus_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.cargus_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.cargus_next_delivery` | Earliest expected delivery; only an account can fill it |
| `sensor.cargus_awaiting_pickup` | Parcels waiting at a pickup point (stays empty until Cargus reports one) |
| `sensor.cargus_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.cargus_last_successful_update` | Diagnostic: when Cargus was last polled successfully |
| `sensor.cargus_outgoing_parcels` | Account only: parcels you sent that are still on their way |
| `sensor.cargus_outgoing_delivered_parcels` | Account only: recently delivered parcels you sent |

An account entry's entities carry the account in their names, for example `sensor.cargus_<account>_incoming_parcels`. A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Cargus has created the shipment but no scan has arrived yet |
| `in_transit` | Collected from the sender and moving through Cargus warehouses |
| `out_for_delivery` | With the courier |
| `delivered` | Delivered |
| `problem` | A delivery attempt failed (nobody home, address not found) |
| `unknown` | Not found yet, or a wording we have not mapped yet |

`at_pickup_point` and `returning` are not reported yet: Cargus has not been seen using a wording for them.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Cargus device):

| Event | When |
|---|---|
| `cargus_parcel_registered` | A new parcel appears in the active list |
| `cargus_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `cargus_parcel_delivered` | A parcel is delivered |
| `cargus_parcel_delivery_time_changed` | Account only: a parcel's expected delivery window changes (`old_planned_from` / `new_planned_from` and the `_to` pair in the payload) |
| `cargus_outgoing_parcel_status_changed` | Account only: a parcel you sent changes status |
| `cargus_outgoing_parcel_delivered` | Account only: a parcel you sent is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `cargus.track_parcel` | `tracking_code` | Start tracking a parcel |
| `cargus.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

Both act on the tracking-codes hub; an account finds its parcels itself.

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.cargus: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Cargus has no record of that AWB yet (it answers empty until the first scan), or the number is wrong. Very old parcels also expire and answer empty. It will pick up automatically once scanned.
- **MyCargus does not accept the phone number** — use the number exactly as the MyCargus app shows it on your account; try `+40…` if the local form is refused.
- **Asked to sign in again** — MyCargus no longer accepts the stored password (it was changed, for example). Enter the current one in the repair prompt.
- **Cargus stopped answering, or a repair says it refuses requests** — the route this integration uses can change without notice, and the integration can stop working until an update ships. Check for a new release and open an issue if there is none. Parcels keep their last known status meanwhile.
- **A status logs "Unrecognised Cargus status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-cargus/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Cargus, Home Assistant, or any other third party referenced in this project. Please don't contact Cargus for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Cargus's terms. Data is sent only to Cargus's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses unofficial routes: a tracking route with no account, and the MyCargus account backend with your own login. Neither is an official API, and either may change or be withdrawn at any time.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
