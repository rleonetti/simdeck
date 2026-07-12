"""
Effects library.

Each effect class receives a LightRig and calls rig.get("name") to get the
lights it needs. All effects implement:
    __init__(self, rig, **kwargs)
    update(self, telemetry: dict) -> None

To add a new effect:
  1. Create a class following the interface above
  2. Register it in EFFECTS at the bottom of this file
  3. Add its name to ACTIVE_EFFECTS in config.py
"""

import logging
import time

from light_rig import LightRig

logger = logging.getLogger(__name__)

HSBK_MAX = 65535

HUE_RED    = 0
HUE_YELLOW = 10922
HUE_GREEN  = 21845
HUE_BLUE   = 43690  # ~240°
HUE_AMBER  = 6371   # ~35° orange-amber


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * max(0.0, min(1.0, t)))


# Named color scheme presets.
# Each scheme has gradient stops [(norm_0_to_1, hue)] and a redline flash color.
COLOR_SCHEMES: dict[str, dict] = {
    "classic": {
        "stops":     [(0.0, HUE_GREEN), (0.6, HUE_YELLOW), (1.0, HUE_RED)],
        "flash_hue": HUE_RED,
        "flash_sat": HSBK_MAX,
        "flash_kel": 3500,
    },
    "porsche": {
        "stops":     [(0.0, HUE_GREEN), (0.6, HUE_YELLOW), (1.0, HUE_RED)],
        "flash_hue": HUE_BLUE,
        "flash_sat": HSBK_MAX,
        "flash_kel": 6500,
    },
    "formula": {
        "stops":     [(0.0, HUE_RED), (1.0, HUE_RED)],
        "flash_hue": HUE_BLUE,
        "flash_sat": HSBK_MAX,
        "flash_kel": 6500,
    },
    "icy": {
        "stops":     [(0.0, HUE_GREEN), (1.0, HUE_BLUE)],
        "flash_hue": 0,
        "flash_sat": 0,
        "flash_kel": 9000,
    },
}


def _hue_at(norm: float, stops: list[tuple[float, int]]) -> int:
    """Interpolate hue from a list of (position, hue) gradient stops."""
    if norm <= stops[0][0]:
        return stops[0][1]
    if norm >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        p0, h0 = stops[i]
        p1, h1 = stops[i + 1]
        if p0 <= norm <= p1:
            return _lerp(h0, h1, (norm - p0) / (p1 - p0))
    return stops[-1][1]


def _rpm_to_hsbk(rpm_pct: float, scheme: dict | None = None) -> tuple[int, int, int, int]:
    """Map 0-1 RPM fraction to HSBK using the given color scheme."""
    if scheme is None:
        scheme = COLOR_SCHEMES["classic"]
    hue = _hue_at(rpm_pct, scheme["stops"])
    brightness = _lerp(HSBK_MAX // 3, HSBK_MAX, rpm_pct)
    return hue, HSBK_MAX, brightness, 3500


# ---------------------------------------------------------------------------
# Rev Counter Effect (LED strip)
# ---------------------------------------------------------------------------

class RevCounterEffect:
    """
    Progressive zone-based rev counter on the LED strip.

    Zones fill left-to-right or from both ends to the middle as RPM climbs.
    The full green→red sweep is remapped to the [start_rpm → redline] window.
    Below the start threshold a single green edge zone shows the system is active.

    COUNTER_MODE options:
      "left_right" — zones fill left to right
      "center"     — zones fill from both ends toward the middle (Porsche-style)
      "full"       — whole strip, one colour; LED_STEP scales brightness

    Reads from rig: "strip"
    Expected telemetry: rpm, max_rpm
    """

    @classmethod
    def needed_lights(cls, rev_counter_lights=None, **_) -> list[str]:
        return list(rev_counter_lights or ["strip"])

    def __init__(
        self,
        rig: LightRig,
        rev_counter_lights: list[str] | None = None,
        start_rpm: int = 0,
        start_threshold: float = 0.50,
        redline_threshold: float = 0.92,
        flash_interval: float = 0.08,
        transition_ms: int = 50,
        led_step: int = 1,
        counter_mode: str = "center",
        strip_reversed: bool = False,
        strip_max_brightness: float = 1.0,
        color_scheme: str = "classic",
        **_,
    ):
        self.controller = rig.get((rev_counter_lights or ["strip"])[0])
        self.start_rpm = start_rpm
        self.start_threshold = start_threshold
        self.redline_threshold = redline_threshold
        self.flash_interval = flash_interval
        self.transition_ms = transition_ms
        self.led_step = max(1, led_step)
        self.counter_mode = counter_mode
        self.strip_reversed = strip_reversed
        self.strip_max_brightness = max(0.0, min(1.0, strip_max_brightness))
        self._scheme = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["classic"])
        self._flash_on = False
        self._last_flash = 0.0

    def _start_pct(self, max_rpm: float) -> float:
        if self.start_rpm > 0 and max_rpm > 0:
            return self.start_rpm / max_rpm
        return self.start_threshold

    def update(self, telemetry: dict) -> None:
        rpm = float(telemetry.get("rpm") or 0)
        max_rpm = float(telemetry.get("max_rpm") or 0)
        if max_rpm <= 0:
            return

        rpm_pct = min(rpm / max_rpm, 1.0)
        start_pct = self._start_pct(max_rpm)

        if rpm_pct >= self.redline_threshold:
            self._redline_flash()
            return

        num_zones = self.controller.num_zones if self.controller else 0

        if rpm_pct < start_pct:
            self._show_ready(num_zones)
            return

        active = max(self.redline_threshold - start_pct, 0.01)
        norm = (rpm_pct - start_pct) / active
        hue, sat, bri, kel = _rpm_to_hsbk(norm, self._scheme)
        bri = int(bri * self.strip_max_brightness)

        if num_zones == 0 or self.counter_mode == "full":
            self.controller.set_color_zoned(hue, sat, bri, kel, self.transition_ms, self.led_step)
        elif self.counter_mode == "left_right":
            self._fill_left_right(norm, hue, sat, bri, kel, num_zones)
        else:
            self._fill_center(norm, hue, sat, bri, kel, num_zones)

    def _show_ready(self, num_zones: int) -> None:
        hue, sat, bri, kel = _rpm_to_hsbk(0.0, self._scheme)
        bri = int(bri * self.strip_max_brightness)
        if not self.controller:
            return
        if num_zones == 0 or self.counter_mode == "full":
            self.controller.set_color_zoned(hue, sat, bri, kel, self.transition_ms, self.led_step)
        elif self.counter_mode == "left_right":
            edge = num_zones - 1 if self.strip_reversed else 0
            other_start, other_end = (0, num_zones - 2) if self.strip_reversed else (1, num_zones - 1)
            self.controller.set_zone_range(edge, edge, hue, sat, bri, kel)
            self.controller.set_zone_range(other_start, other_end, 0, 0, 0, 3500)
        else:  # center — symmetric, reversal has no effect
            self.controller.set_zone_range(0, 0, hue, sat, bri, kel)
            self.controller.set_zone_range(num_zones - 1, num_zones - 1, hue, sat, bri, kel)
            self.controller.set_zone_range(1, num_zones - 2, 0, 0, 0, 3500)

    def _fill_left_right(self, norm, hue, sat, bri, kel, num_zones):
        lit = max(1, round(norm * num_zones))
        if self.strip_reversed:
            start = num_zones - lit
            self.controller.set_zone_range(start, num_zones - 1, hue, sat, bri, kel)
            self.controller.set_zone_range(0, start - 1, 0, 0, 0, 3500)
        else:
            self.controller.set_zone_range(0, lit - 1, hue, sat, bri, kel)
            self.controller.set_zone_range(lit, num_zones - 1, 0, 0, 0, 3500)

    def _fill_center(self, norm, hue, sat, bri, kel, num_zones):
        half = num_zones // 2
        lit_per_side = max(1, round(norm * half))
        self.controller.set_zone_range(0, lit_per_side - 1, hue, sat, bri, kel)
        self.controller.set_zone_range(num_zones - lit_per_side, num_zones - 1, hue, sat, bri, kel)
        self.controller.set_zone_range(lit_per_side, num_zones - lit_per_side - 1, 0, 0, 0, 3500)

    def _redline_flash(self) -> None:
        now = time.monotonic()
        if now - self._last_flash >= self.flash_interval:
            self._flash_on = not self._flash_on
            self._last_flash = now
        peak = int(HSBK_MAX * self.strip_max_brightness)
        brightness = peak if self._flash_on else peak // 4
        fhue = self._scheme["flash_hue"]
        fsat = self._scheme["flash_sat"]
        fkel = self._scheme["flash_kel"]
        num_zones = self.controller.num_zones if self.controller else 0
        if num_zones > 0:
            self.controller.set_zone_range(0, num_zones - 1, fhue, fsat, brightness, fkel)
        elif self.controller:
            self.controller.set_color(fhue, fsat, brightness, fkel, 0)


# ---------------------------------------------------------------------------
# Rev Lights Effect (LED strip — whole-strip fallback)
# ---------------------------------------------------------------------------

class RevLightsEffect:
    """
    Whole-strip colour shift: green → yellow → red.
    No zone control; LED_STEP scales brightness.
    Use RevCounterEffect with counter_mode="full" for the same look with zone support.

    Reads from rig: "strip"
    Expected telemetry: rpm, max_rpm
    """

    @classmethod
    def needed_lights(cls, **_) -> list[str]:
        return ["strip"]

    def __init__(
        self,
        rig: LightRig,
        start_rpm: int = 0,
        start_threshold: float = 0.50,
        redline_threshold: float = 0.92,
        flash_interval: float = 0.08,
        transition_ms: int = 50,
        led_step: int = 1,
        strip_max_brightness: float = 1.0,
        color_scheme: str = "classic",
        **_,
    ):
        self.controller = rig.get("strip")
        self.start_rpm = start_rpm
        self.start_threshold = start_threshold
        self.redline_threshold = redline_threshold
        self.flash_interval = flash_interval
        self.transition_ms = transition_ms
        self.led_step = max(1, led_step)
        self.strip_max_brightness = max(0.0, min(1.0, strip_max_brightness))
        self._scheme = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["classic"])
        self._flash_on = False
        self._last_flash = 0.0

    def _start_pct(self, max_rpm: float) -> float:
        if self.start_rpm > 0 and max_rpm > 0:
            return self.start_rpm / max_rpm
        return self.start_threshold

    def update(self, telemetry: dict) -> None:
        rpm = float(telemetry.get("rpm") or 0)
        max_rpm = float(telemetry.get("max_rpm") or 0)
        if max_rpm <= 0:
            return

        rpm_pct = min(rpm / max_rpm, 1.0)
        start_pct = self._start_pct(max_rpm)

        if rpm_pct >= self.redline_threshold:
            self._redline_flash()
            return

        if rpm_pct < start_pct:
            hue, sat, bri, kel = _rpm_to_hsbk(0.0, self._scheme)
        else:
            active = max(self.redline_threshold - start_pct, 0.01)
            norm = (rpm_pct - start_pct) / active
            hue, sat, bri, kel = _rpm_to_hsbk(norm, self._scheme)
        bri = int(bri * self.strip_max_brightness)

        if self.controller:
            self.controller.set_color_zoned(hue, sat, bri, kel, self.transition_ms, self.led_step)

    def _redline_flash(self) -> None:
        now = time.monotonic()
        if now - self._last_flash >= self.flash_interval:
            self._flash_on = not self._flash_on
            self._last_flash = now
        peak = int(HSBK_MAX * self.strip_max_brightness)
        brightness = peak if self._flash_on else peak // 4
        fhue = self._scheme["flash_hue"]
        fsat = self._scheme["flash_sat"]
        fkel = self._scheme["flash_kel"]
        if self.controller:
            self.controller.set_color_zoned(fhue, fsat, brightness, fkel, 0, self.led_step)


# ---------------------------------------------------------------------------
# Brake Lights Effect (downlights)
# ---------------------------------------------------------------------------

class BrakeLightsEffect:
    """
    Illuminates downlights red on braking, with brightness proportional to
    brake pressure. Restores to idle when brake is released.

    Reads from rig: whichever names are listed in BRAKE_LIGHTS config.
    Expected telemetry: brake (0.0–1.0)
    """

    @classmethod
    def needed_lights(cls, brake_lights: list[str] | None = None, **_) -> list[str]:
        return list(brake_lights or [])

    def __init__(
        self,
        rig: LightRig,
        brake_lights: list[str] | None = None,
        brake_threshold: float = 0.05,
        brake_max_brightness: float = 1.0,
        **_,
    ):
        names = brake_lights or []
        self._lights = [rig.get(name) for name in names if rig.get(name)]
        self.brake_threshold = brake_threshold
        self.brake_max_brightness = max(0.0, min(1.0, brake_max_brightness))
        self._was_braking = False
        self._logged_first_brake = False

        connected = [name for name in names if rig.get(name) and rig.get(name)._light]
        registered = [name for name in names if rig.get(name)]
        missing = [name for name in names if not rig.get(name)]
        logger.success("BrakeLightsEffect: %d/%d lights connected: %s", len(connected), len(names), connected)
        if missing:
            logger.warning("BrakeLightsEffect: lights not registered in LIFX_LIGHTS: %s", missing)
        if len(registered) > len(connected):
            failed = [n for n in registered if not rig.get(n)._light]
            logger.warning("BrakeLightsEffect: lights registered but failed to connect: %s", failed)

    def update(self, telemetry: dict) -> None:
        brake = float(telemetry.get("brake") or 0) / 100.0  # SimHub reports 0-100
        is_braking = brake > self.brake_threshold

        if is_braking and not self._logged_first_brake:
            logger.info("Brake event: %.3f (threshold %.2f) — commanding %d lights", brake, self.brake_threshold, len(self._lights))
            self._logged_first_brake = True

        if is_braking:
            brightness = int(brake * HSBK_MAX * self.brake_max_brightness)
            for light in self._lights:
                light.set_color(HUE_RED, HSBK_MAX, brightness, 3500, 0)
        elif self._was_braking:
            for light in self._lights:
                light.set_idle()

        self._was_braking = is_braking


# ---------------------------------------------------------------------------
# Flag Effect (ceiling downlights)
# ---------------------------------------------------------------------------

# Evaluated highest-priority first. Each entry: (hue, sat, kelvin, flash_interval_s)
# flash_interval=0 means solid (no flashing).
_FLAG_PRIORITY = [
    "flag_black", "flag_red", "flag_blue", "flag_yellow",
    "flag_white", "flag_checkered", "flag_green",
]
_FLAG_SPECS: dict[str, tuple[int, int, int, float]] = {
    "flag_black":     (HUE_RED,    HSBK_MAX, 3500, 0.15),  # rapid red   — penalty/DSQ
    "flag_red":       (HUE_RED,    HSBK_MAX, 3500, 0.0),   # solid red   — session stopped
    "flag_blue":      (HUE_BLUE,   HSBK_MAX, 6500, 0.2),   # fast blue   — let faster car past
    "flag_yellow":    (HUE_YELLOW, HSBK_MAX, 3500, 0.5),   # slow yellow — caution
    "flag_white":     (0,          0,        9000, 1.0),   # slow white  — last lap
    "flag_checkered": (0,          0,        9000, 0.25),  # fast white  — finish
    "flag_green":     (HUE_GREEN,  HSBK_MAX, 3500, 0.0),   # solid green — race start/restart
}


class FlagEffect:
    """
    Flashes ceiling downlights to match the current race flag.

    Priority (highest first): black, red, blue, yellow, white, checkered.
    When no flag is active the lights return to idle.

    Reads from rig: lights listed in flag_lights
    Expected telemetry: flag_yellow, flag_red, flag_blue, flag_white,
                        flag_checkered, flag_black  (all 0 or 1)
    """

    @classmethod
    def needed_lights(cls, flag_lights: list[str] | None = None, **_) -> list[str]:
        return list(flag_lights or [])

    def __init__(
        self,
        rig: LightRig,
        flag_lights: list[str] | None = None,
        flag_max_brightness: float = 1.0,
        enabled_flags: list[str] | None = None,
        **_,
    ):
        names = flag_lights or []
        self._lights = [rig.get(n) for n in names if rig.get(n)]
        self._brightness = max(0.0, min(1.0, flag_max_brightness))
        self._enabled: set[str] | None = set(enabled_flags) if enabled_flags is not None else None
        self._flash_on = False
        self._last_flash = 0.0
        self._was_active = False

    def update(self, telemetry: dict) -> None:
        active_flag = None
        for key in _FLAG_PRIORITY:
            if self._enabled is not None and key not in self._enabled:
                continue
            if telemetry.get(key):
                active_flag = key
                break

        if active_flag is None:
            if self._was_active:
                for light in self._lights:
                    light.set_idle()
                self._was_active = False
            return

        self._was_active = True
        hue, sat, kel, interval = _FLAG_SPECS[active_flag]
        peak = int(HSBK_MAX * self._brightness)

        now = time.monotonic()
        if interval > 0:
            if now - self._last_flash >= interval:
                self._flash_on = not self._flash_on
                self._last_flash = now
            bri = peak if self._flash_on else 0
        else:
            bri = peak

        for light in self._lights:
            light.set_color(hue, sat, bri, kel, 0)


# ---------------------------------------------------------------------------
# Pit Limiter Effect (strip or ceiling lights)
# ---------------------------------------------------------------------------

class PitLimiterEffect:
    """
    Flashes amber while the pit speed limiter is active.
    Defaults to the LED strip so it overrides the rev counter visually.
    Deactivates cleanly — strip returns to idle, letting rev counter resume.

    Reads from rig: lights listed in pit_limiter_lights
    Expected telemetry: pit_limiter (0 or 1)
    """

    @classmethod
    def needed_lights(cls, pit_limiter_lights: list[str] | None = None, **_) -> list[str]:
        return list(pit_limiter_lights or ["strip"])

    def __init__(
        self,
        rig: LightRig,
        pit_limiter_lights: list[str] | None = None,
        pit_limiter_brightness: float = 0.75,
        pit_limiter_flash_interval: float = 0.25,
        **_,
    ):
        names = pit_limiter_lights or ["strip"]
        self._lights = [rig.get(n) for n in names if rig.get(n)]
        self._brightness = max(0.0, min(1.0, pit_limiter_brightness))
        self._flash_interval = pit_limiter_flash_interval
        self._flash_on = False
        self._last_flash = 0.0
        self._was_active = False

    def update(self, telemetry: dict) -> None:
        active = bool(telemetry.get("pit_limiter"))

        if not active:
            if self._was_active:
                for light in self._lights:
                    light.set_idle()
                self._was_active = False
            return

        self._was_active = True
        now = time.monotonic()
        if now - self._last_flash >= self._flash_interval:
            self._flash_on = not self._flash_on
            self._last_flash = now

        peak = int(HSBK_MAX * self._brightness)
        bri = peak if self._flash_on else 0

        for light in self._lights:
            num_zones = getattr(light, "num_zones", 0)
            if num_zones > 0:
                light.set_zone_range(0, num_zones - 1, HUE_AMBER, HSBK_MAX, bri, 3500, 0)
            else:
                light.set_color(HUE_AMBER, HSBK_MAX, bri, 3500, 0)


# ---------------------------------------------------------------------------
# Registry — add new effects here
# ---------------------------------------------------------------------------

EFFECTS: dict[str, type] = {
    "rev_counter":  RevCounterEffect,
    "rev_lights":   RevLightsEffect,
    "brake_lights": BrakeLightsEffect,
    "flag_effect":  FlagEffect,
    "pit_limiter":  PitLimiterEffect,
}
