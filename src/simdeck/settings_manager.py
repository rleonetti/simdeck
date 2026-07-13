"""
Persists user settings to %APPDATA%/SimDeck/settings.json.

config.py provides the defaults. Any key saved here overrides the default.
The app writes immediately on every change — no Save button needed.
"""

import json
from pathlib import Path

from . import config

_DIR  = Path.home() / "Documents" / "SimDeck"
_FILE = _DIR / "settings.json"

DEFAULTS: dict = {
    "active_effects":       list(config.ACTIVE_EFFECTS),
    "color_scheme":         "classic",
    "counter_mode":         config.COUNTER_MODE,
    "strip_reversed":       config.STRIP_REVERSED,
    "led_step":             config.LED_STEP,
    "start_rpm":            config.REV_START_RPM,
    "redline_pct":          int(config.REV_REDLINE_THRESHOLD * 100),
    "strip_brightness_pct": int(config.STRIP_MAX_BRIGHTNESS * 100),
    "brake_threshold_pct":          int(config.BRAKE_THRESHOLD * 100),
    "brake_brightness_pct":         int(config.BRAKE_MAX_BRIGHTNESS * 100),
    "flag_brightness_pct":          int(config.FLAG_MAX_BRIGHTNESS * 100),
    "pit_limiter_lights_label":     "Strip",
    "pit_limiter_brightness_pct":   int(config.PIT_LIMITER_BRIGHTNESS * 100),
    "splitter_port":        20777,
    "splitter_targets": [
        {"ip": "127.0.0.1", "port": 20066, "label": "Moza Pit House", "enabled": True},
        {"ip": "127.0.0.1", "port": 8000,  "label": "SimHub",         "enabled": True},
    ],
    # Per-flag enable/disable
    "flags_enabled": {
        "flag_yellow":    True,
        "flag_red":       True,
        "flag_blue":      True,
        "flag_white":     True,
        "flag_green":     True,
        "flag_checkered": True,
        "flag_black":     True,
    },
    # App-level settings
    "font_size_pt":    10,
    "lights": [],
    "effect_lights": {
        "rev_counter":  [],
        "brake_lights": [],
        "flag_effect":  [],
        "pit_limiter":  [],
    },
    "accent_color":    "#f0a500",
    "start_minimized": False,
    "simhub_host":     "127.0.0.1",
    "simhub_port":     18082,
}


def load() -> dict:
    """Return defaults merged with any saved overrides. Creates the file on first run."""
    settings = dict(DEFAULTS)
    if _FILE.exists():
        try:
            with _FILE.open() as f:
                settings.update(json.load(f))
        except Exception:
            pass
    else:
        save(settings)
    return settings


def save(settings: dict) -> None:
    """Write settings to disk immediately."""
    _DIR.mkdir(parents=True, exist_ok=True)
    with _FILE.open("w") as f:
        json.dump(settings, f, indent=2)
