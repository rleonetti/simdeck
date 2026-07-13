# Changelog

## v1.4.0 — 2026-07-13
### Changed
- **Full internal rewrite** — `simdeck.py` (3688-line monolith) split into a proper `src/simdeck/ui/` package: `constants`, `theme`, `helpers`, `widgets`, `overlay`, `app`, and a `tabs/` sub-package (`lifx`, `lights`, `test`, `splitter`, `logger`, `history`, `settings`); no user-visible behaviour changes
- Moved all source into `src/simdeck/` layout with setuptools editable install; `python -m simdeck` and `simdeck` script entry point both work

## v1.3.2 — 2026-07-13
### Added
- **Gradient background** — the main window background fades diagonally from near-black (top-left) to a muted accent color (bottom-right); toggle with the new "Gradient background" checkbox in Settings → APPEARANCE; defaults to on; the gradient tracks the accent color picker live

## v1.3.1 — 2026-07-12
### Fixed
- **Tray exit no longer hangs or leaves a ghost overlay** — `_quit()` now stops the overlay timer and hides the overlay immediately, signals all background threads without joining them (all threads are daemon and die with the process), and uses `os._exit(0)` to guarantee the process exits regardless of any thread state; previously `engine.stop()` blocked the Qt main thread for up to 5 seconds while joining a socket thread, during which the overlay remained visible

## v1.3.0 — 2026-07-12
### Changed
- **Moza pedal integration rewritten to use USB HID** — reads directly from the R12 Base joystick interface (VID=0x346E, PID=0x0016) instead of the serial protocol; HID is shared-access so it works alongside Pit House with no port conflicts
- Replaced `pyserial` dependency with `hidapi`

### Added
- **Clutch trace** (blue) added to the telemetry overlay; both Mirrored and Lines themes now show throttle (green), brake (red), and clutch (blue)

## v1.2.9 — 2026-07-12
### Added
- **Moza pedal integration** — reads `throttle-output` and `brake-output` directly from Moza hardware via USB serial at ~100 Hz; overlay automatically switches to hardware data when pedals are detected, falls back to SimHub telemetry otherwise
- Moza status indicator (● dot + label) in Settings → OVERLAY showing connected port or "searching…"
- `moza_pedals.py` — standalone module implementing the Moza serial protocol (protocol reference: boxflat)
- `pyserial` added to requirements

## v1.2.8 — 2026-07-12
### Added
- Overlay style dropdown in Settings → OVERLAY: **Mirrored** (throttle fill above / brake fill below center line) and **Lines** (both traces rise from baseline, no fill); selection persists between sessions

## v1.2.7 — 2026-07-12
### Changed
- Overlay redrawn as lines-only (no fill): throttle (green) and brake (red) both rise from baseline in the same plane
- Added separate Background opacity, Line opacity, and Scale (50–200%) sliders under Settings → OVERLAY; line width scales with overlay size

## v1.2.6 — 2026-07-12
### Added
- **Telemetry overlay** — floating, always-on-top, transparent window showing a real-time scrolling throttle/brake graph; throttle (green) fills upward from center, brake (red) fills downward; draggable, position saved between sessions; toggle and opacity slider under Settings → OVERLAY

## v1.2.5 — 2026-07-12
### Added
- Vehicle and track shown in Logger tab during active recording (suppresses internal codes like Forza's `DT__*` track identifiers)

### Fixed
- Empty sessions from loading-screen start/stop cycles are automatically deleted on recording stop — no more blank rows in History

## v1.2.4 — 2026-07-12
### Added
- Auto Record defaults to enabled — recording starts automatically when SimHub connects, no manual step needed
- Game detection runs at startup (previously only every 20 seconds), so the session is tagged with the correct game name from the first lap
- `flush_pending_lap()`: when telemetry goes silent at race end (Forza stops sending data rather than sending a lap-end signal), the last known lap time is saved automatically

### Fixed
- Single-lap races in Forza Horizon 6 are now recorded — the detection correctly captures `current_lap_time` at the moment telemetry stops, gated to Forza sessions only to avoid false positives in other games

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
