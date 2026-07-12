# SimDeck

A sim racing companion app that fills the gaps left by SimHub and other tools. Currently handles LIFX ambient lighting driven by telemetry and UDP forwarding to multiple apps simultaneously — with more features planned.

Runs as a Windows system-tray app. Settings persist between sessions. Installer available — no admin rights required.

---

## Features

### Lights tab

Manage which LIFX lights SimDeck controls and which effects each one participates in.

- **Registry** — add lights by name and IP (manual or via Scan Network); Edit and Remove are also available
- **Scan Network** — discovers all LIFX lights on your subnet via unicast sweep; already-registered lights are hidden so only new ones appear
- **Effect Assignments** — checkboxes assign each registered light to Rev Counter, Brake Lights, Flag Effect, and/or Pit Limiter independently

Settings are saved automatically. No config file editing required.

---

### LIFX Effects tab

Controls LIFX lights in real time using telemetry from SimHub. The lights used by each effect are configured in the Lights tab.

**Rev Counter** — fills an LED strip with colour as RPM climbs, with a configurable redline flash
- **Modes:** Center Fill (Porsche-style, fills from both ends inward), Left → Right, or Full Strip
- **Color Schemes:** Classic (green → red, red flash), Porsche (green → red, blue flash), Formula (red only, blue flash), Icy (green → blue, white flash)
- Configurable start RPM, redline %, max brightness, LED step (intensity for Full mode), and direction reversal

**Brake Lights** — illuminates ceiling downlights red proportional to brake pressure, with configurable threshold and brightness

**Flag Effect** — flashes ceiling lights to match in-game flags (yellow, blue, etc.)

**Pit Limiter** — flashes the strip, ceiling lights, or both when the pit limiter is active

Multiple effects run simultaneously every frame. The status bar shows live RPM, max RPM, and brake % when SimHub is connected, and a per-light connection indicator shows which LIFX lights are reachable.

---

### UDP Splitter tab

Receives a single UDP telemetry stream (e.g. from a game or SimHub) and forwards it to multiple destinations simultaneously.

- Add as many forward targets as needed, each with an IP, port, and optional label
- Enable or disable individual targets without removing them
- **Game associations** — optionally restrict a target to only receive data when a specific game is running. Targets with no game set always receive data
- **Auto-manage** — when enabled, game associations are applied automatically based on which game is detected as running. Disable to forward to all enabled targets regardless of game
- Supports Forza, Assetto Corsa EVO/Rally/Competizione/original, BeamNG, iRacing, rFactor 2

Useful for running SimHub, motion platforms (Moza, SimXperience, etc.), and custom tools off the same telemetry port.

---

### Test tab

Connects directly to LIFX (bypassing the live engine) for animation testing without needing a game running. Four dedicated panels let you test each effect independently:

- **Rev Counter** — choose animation mode and scrub the RPM slider
- **Brake Lights** — scrub brake pressure
- **Flags** — trigger any flag type with a single click
- **Pit Limiter** — toggle on/off

Useful for dialing in brightness, colours, and timing before a session.

---

### Settings tab

- **Font size** — adjusts text size across the whole app
- **Accent color** — color picker that changes the highlight color used for sliders, checkboxes, and the active tab underline. Takes effect immediately and persists between sessions
- **Updates** — check for updates manually; when one is available, clicking **Update to vX.X.X** downloads the installer, quits SimDeck, and launches the installer automatically
- **Startup** — launch at Windows startup and/or start minimized to tray
- **Connection** — SimHub Property Server host and port

---

## Installation

### Windows installer (recommended)

Download `SimDeck-x.x.x-setup.exe` from the [Releases page](https://github.com/rleonetti/simdeck/releases). Installs per-user — no admin rights or UAC prompt required. An optional startup entry lets SimDeck launch with Windows.

SimDeck checks for updates on startup. When a newer version is available the tray icon and Settings tab both show a notification. Clicking **Update to vX.X.X** downloads the installer in the background, quits the app, and launches the installer — no manual steps required.

### Run from source

```
pip install -r requirements.txt
python simdeck.py
```

---

## Setup

### SimHub Property Server (required for LIFX effects)

SimDeck reads telemetry over TCP from the [SimHub Property Server](https://github.com/pre-martin/SimHubPropertyServer) plugin.

1. Download `PropertyServer.dll` from the [releases page](https://github.com/pre-martin/SimHubPropertyServer/releases)
2. Copy it into your SimHub folder (e.g. `C:\Program Files (x86)\SimHub\`)
3. Restart SimHub → **Settings → Plugins** → enable **SimHub Property Server**
4. Restart SimHub — it will now listen on TCP port 18082

### LIFX lights

Add your lights in the **Lights tab** — either type the IP directly with **+ Add**, or use **Scan Network** to discover all LIFX devices on your subnet automatically. Give each light a name, then use the effect assignment checkboxes to control which effects it participates in.

Light settings are stored in `%USERPROFILE%\Documents\SimDeck\settings.json` and persist between sessions.

> **Upgrading from an older version?** If you previously configured lights in `config_local.py`, SimDeck will automatically import them into the registry on first launch.

---

## Architecture

```
SimHub (game telemetry)
    │  TCP port 18082 (SimHub Property Server)
    ▼
simhub_client.py  — subscribes to rpm, max_rpm, brake, flags, pit limiter, etc.
    │
    ▼
engine.py  — runs active effects at 20 Hz; drops to 1 Hz when idle
    │
    ├── effects.py      — maps telemetry to LIFX commands
    └── lifx_controller.py  — sends LIFX LAN protocol to lights

Game UDP telemetry (port configurable)
    │
    ▼
udp_splitter.py  — fans out to all enabled targets
```

---

## Roadmap / Ideas

> This section tracks potential additions. Open an issue or PR if you want to contribute.

- Additional games in the auto-detect list
- SimHub shake / rumble integration
- Dashboard overlays (speed, gear, delta)
- Button box / Stream Deck profile integration
- Rev lights for non-LIFX hardware
