# Pascal IP Amplifier — Home Assistant integration

Control and monitor [Pascal Audio](https://pascal-audio.com) IP amplifiers
(PZC / ODM series) from Home Assistant over the local network. No cloud, no
polling-only — the integration keeps a live TCP connection and receives pushed
updates from the amplifier.

> Status: initial release (`0.1.0`). Built against the *Open API for Installers*
> v1.8 (firmware 1.x).

## Features

- **Auto-discovery** via mDNS (`_pasconnect._tcp`) plus manual IP entry.
- **One media player per zone** with:
  - volume (mapped from the zone gain limits `GAIN_MIN`…`GAIN_MAX`),
  - mute,
  - source selection (analog / SPDIF / Dante / mixes, respecting the per-zone
    source allow-list),
  - on/off (mutes the zone; whole-amp power is a separate switch).
- **Power switch** for the whole amplifier (`POWER_ON` / `POWER_OFF`).
- **Status sensors**: amplifier state, input signal, output signal.
- **Diagnostic sensors** (disabled by default): firmware, API version, serial,
  LAN / Wi-Fi IP.
- **Per-channel signal-level meters and clip indicators** (disabled by default
  to keep the recorder light — enable the ones you want).
- **Fault binary sensor**.

## Robustness

This integration is written so a misbehaving or offline amplifier can never
crash Home Assistant:

- A single background reader parses every line; any malformed line is logged and
  skipped, never raised.
- Every value from the amplifier is parsed defensively (`safe_float`,
  `safe_int`, `safe_bool`); bad data yields `None`/unavailable, not an exception.
- The connection is supervised and reconnects automatically with exponential
  backoff. While disconnected, entities report *unavailable*.
- Outgoing commands are serialised and time out instead of hanging.
- A periodic heartbeat doubles as a liveness check.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories* → add this repo, category *Integration*.
2. Install **Pascal IP Amplifier**, then restart Home Assistant.

### Manual

Copy `custom_components/pascal_amp/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Settings → Devices & Services → *Add Integration* → **Pascal IP Amplifier**.
Discovered amplifiers appear automatically; otherwise enter the IP address
(default port `7621`).

Out of the box the amplifier ships on `192.168.64.100` (Ethernet) or
`192.168.4.1` (its Wi-Fi AP).

## Testing connectivity without Home Assistant

```powershell
python tools/probe.py 192.168.64.100
python tools/probe.py 192.168.64.100 --subscribe 5
```

This dumps every register and (optionally) streams live updates, using the same
line-based protocol the integration uses.

## Notes / limitations

- Volume is read-only for a zone whose gain is bound to a GPIO volume control;
  the integration hides the volume-set feature in that case.
- Muting relies on `ZONE-x.MUTE`. If the amplifier has *Mute enable* turned off
  for a zone, the device may ignore the command.
- Level meters are subscription-only and update at the amplifier's dynamic rate;
  they are coalesced into Home Assistant state writes, so expect ~second-level
  granularity, not metering-grade.
- Advanced DSP (EQ, crossover, limiters, FIR, routing) is intentionally not
  exposed; those are configured with the manufacturer's tools.

## License

See repository.
