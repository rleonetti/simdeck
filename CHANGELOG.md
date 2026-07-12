# Changelog

## v1.2.3 — 2026-07-12
### Fixed
- Engine connection: replaced `get_devices()` (which called `discover_devices()` → `is_switch()` → `get_version_tuple()`, making 4+ network calls per light and causing `WorkflowException` on slow responders) with a single `GetService` query per light
- Multi-zone detection now calls `supports_multizone()` directly instead of relying on `isinstance(MultiZoneLight)` set by `discover_devices()`

### Changed
- Scan Network performs a full unicast sweep of the /24 subnet derived from registry IPs (bypasses Windows Firewall blocking UDP broadcast); batches of 100 with 2 attempts catch slow responders reliably (~10s)
- Already-registered lights are hidden from scan results; a note shows how many were skipped

## v1.2.2 — 2026-07-12
### Added
- On first launch with an empty Lights registry, lights are automatically imported from `config_local.LIFX_LIGHTS` if present

### Fixed
- Scan Network now falls back to direct unicast connections for registry IPs when broadcast is blocked by Windows Firewall

## v1.2.1 — 2026-07-12
### Fixed
- `SyntaxWarning: invalid escape sequence '\S'` on startup from `settings_manager.py` docstring
- `QMetaObject::invokeMethod` error during Lights scan — replaced with `Signal(list)` for thread-safe UI updates
- Rev Counter incorrectly falling back to "strip" when an empty light list was configured (empty list now correctly means no lights)

### Changed
- Lights tab moved between LIFX Effects and UDP Splitter

## v1.2.0 — 2026-07-11
### Added
- **Lights tab** — dynamic light registry (Add/Edit/Remove/Scan) and per-effect assignment checkboxes, replacing hardcoded `config.py` light lists
- `settings.json` now stores `"lights"` and `"effect_lights"` keys
- Engine accepts `lights_config` dict at init and exposes `update_lights_config()` for live updates

## v1.1.2 — 2026-07-10
### Fixed
- "Update to vX.X.X" button text was clipped — removed fixed width so the button auto-sizes

## v1.1.1 — 2026-07-10
### Added
- Auto-install update flow: clicking "Update Now" downloads the installer to `%TEMP%`, quits SimDeck, then launches the installer — eliminates the Inno Setup "close applications" conflict

### Fixed
- Settings were lost between sessions when closing to tray — `closeEvent` now saves before hiding

## v1.1.0 — 2026-07-10
### Added
- Accent color picker in Settings — replaces hardcoded amber `#f0a500` with a live-updating `QColorDialog`; color persists between sessions

## v1.0.x and earlier
Initial releases — LIFX effects, UDP splitter, lap logger, SimHub Property Server integration.
