"""
SimHub LIFX Integration - Configuration

SimHub Setup (do this once):
  1. Download PropertyServer.dll from:
       https://github.com/pre-martin/SimHubPropertyServer/releases
     (click "Releases" on the right — do NOT use the green Download button)
  2. Copy PropertyServer.dll into your SimHub folder, e.g.:
       C:\\Program Files (x86)\\SimHub\\
  3. Restart SimHub, then go to Settings → Plugins
  4. Find "SimHub Property Server" in the list and enable it
  5. Restart SimHub once more — it now listens on TCP port 18082
"""

# --- SimHub Property Server (TCP) ---
SIMHUB_HOST = "127.0.0.1"  # SimHub is always on the same machine
SIMHUB_PORT = 18082          # default PropertyServer port
SIMHUB_IDLE_TIMEOUT = 5.0   # seconds without a property update before going idle

# ---------------------------------------------------------------------------
# LIFX lights
# Each key is the name used to reference the light in effects.
# "ip" connects directly (works cross-VLAN, faster than broadcast).
# Set "ip" to None and provide "label" for broadcast discovery instead.
# ---------------------------------------------------------------------------
LIFX_LIGHTS = {
    "strip": {
        "ip": "192.168.30.126",
    },
    # Downlights — named from the doorway perspective (door is to the left when sitting)
    "front_right": {                        # ceiling, directly left of the desk when sitting
        "ip": "192.168.30.146",             # LIFX-DLCOL-779063
    },
    "front_left": {                         # slightly behind and to the right when sitting
        "ip": "192.168.30.53",              # LIFX-DLCOL-764149
    },
    "rear_left": {                          # rear of the room, left from doorway
        "ip": "192.168.30.73",              # LIFX-DLCOL-76FA28
    },
    "rear_right": {                         # rear of the room, right from doorway
        "ip": "192.168.30.214",             # LIFX-DLCOL-76F1C1
    },
}

LIFX_TRANSITION_MS = 50     # colour transition time per command (lower = more responsive)
LIFX_DISCOVERY_TIMEOUT = 5  # seconds to scan when using broadcast discovery

# ---------------------------------------------------------------------------
# Active effects
# Effects run simultaneously every frame. Each can address any light by name.
# Available: "rev_counter", "rev_lights", "brake_lights"
# ---------------------------------------------------------------------------
ACTIVE_EFFECTS = ["rev_counter", "brake_lights"]

# ---------------------------------------------------------------------------
# Rev counter / rev lights tuning
# ---------------------------------------------------------------------------
REV_START_RPM         = 7500    # Absolute RPM where animation begins — 0 = use fraction below
REV_START_THRESHOLD   = 0.50    # Fraction of max RPM (only used when REV_START_RPM = 0)
REV_REDLINE_THRESHOLD = 0.94    # Fraction of max RPM that triggers the redline flash
REV_FLASH_INTERVAL    = 0.08    # Seconds between flashes at redline
LED_STEP              = 4       # Brightness divisor for "full" mode (1=full, 2=half, 4=quarter)
COUNTER_MODE          = "center"
# COUNTER_MODE options:
#   "center"     — fill from both ends toward the middle (Porsche-style)
#   "left_right" — fill left to right (or right to left if STRIP_REVERSED = True)
#   "full"       — whole strip one colour; LED_STEP controls brightness
STRIP_REVERSED       = True    # True = fill right-to-left (flip if the strip is wired backwards)
STRIP_MAX_BRIGHTNESS = 0.50   # brightness cap for the LED strip (0.0–1.0)

# ---------------------------------------------------------------------------
# Brake lights effect
# ---------------------------------------------------------------------------
BRAKE_LIGHTS          = ["front_right", "front_left", "rear_left", "rear_right"]
BRAKE_THRESHOLD       = 0.05   # minimum brake input (0-1) to activate lights
BRAKE_MAX_BRIGHTNESS  = 0.75   # maximum brightness at full brake (0.0-1.0)

# ---------------------------------------------------------------------------
# Flag effect  (ceiling downlights)
# ---------------------------------------------------------------------------
FLAG_LIGHTS           = ["front_right", "front_left", "rear_left", "rear_right"]
FLAG_MAX_BRIGHTNESS   = 1.0

# ---------------------------------------------------------------------------
# Pit limiter effect
# ---------------------------------------------------------------------------
PIT_LIMITER_LIGHTS      = ["strip"]     # "strip", ceiling lights, or both
PIT_LIMITER_BRIGHTNESS  = 0.75
PIT_LIMITER_FLASH_INTERVAL = 0.25      # seconds per flash half-cycle
