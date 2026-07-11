"""Background thread that connects LIFX lights and runs effects from SimHub telemetry."""

import logging
import threading
import time

import config
from effects import EFFECTS
from lifx_controller import LIFXController
from light_rig import LightRig
from simhub_client import SimHubClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL        = 1 / 20   # 50 ms  — active
_POLL_INTERVAL_DORMANT = 1.0     # 1000 ms — after _DORMANT_AFTER seconds of no telemetry
_DORMANT_AFTER        = 15.0     # seconds of inactivity before slowing down


class Engine:
    """Runs LIFX effects driven by SimHub telemetry in a background thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._light_status: dict[str, str] = {n: "idle" for n in config.LIFX_LIGHTS}
        self._simhub_status = "disconnected"
        self._rig: LightRig | None = None
        self._last_telemetry: dict = {}

    # ------------------------------------------------------------------ public

    def start(self, effect_kwargs: dict) -> None:
        """Full start: connect all needed lights then begin poll loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._rig = None
        with self._lock:
            for name in config.LIFX_LIGHTS:
                self._light_status[name] = "connecting"
            self._simhub_status = "connecting"
        self._thread = threading.Thread(
            target=self._run, args=(effect_kwargs,), daemon=True, name="engine"
        )
        self._thread.start()

    def pause(self) -> None:
        """Stop the poll loop but keep LIFX connections alive for reuse."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        with self._lock:
            self._simhub_status = "disconnected"

    def resume(self, effect_kwargs: dict) -> None:
        """Restart the poll loop using the existing LightRig.
        Connects any lights needed by effect_kwargs that weren't in the original rig."""
        if not self._rig:
            self.start(effect_kwargs)
            return
        self._stop_event.clear()
        with self._lock:
            self._simhub_status = "connecting"
        self._thread = threading.Thread(
            target=self._poll_loop, args=(effect_kwargs,), daemon=True, name="engine"
        )
        self._thread.start()

    def stop(self) -> None:
        """Full stop: end poll loop, set strip idle, release LightRig."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        with self._lock:
            for name in config.LIFX_LIGHTS:
                self._light_status[name] = "idle"
            self._simhub_status = "disconnected"
        if self._rig and self._rig.get("strip"):
            self._rig.get("strip").set_idle()
        self._rig = None

    def restart(self, effect_kwargs: dict) -> None:
        """Full restart: releases rig and reconnects everything fresh."""
        self.stop()
        self.start(effect_kwargs)

    def get_telemetry(self) -> dict:
        """Return the most recent SimHub telemetry frame, or empty dict if none yet."""
        with self._lock:
            return dict(self._last_telemetry)

    def get_rig(self) -> LightRig | None:
        """Return the currently connected LightRig, or None if not yet connected."""
        return self._rig

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_status(self) -> dict:
        with self._lock:
            return {
                "lights": dict(self._light_status),
                "simhub": self._simhub_status,
            }

    # ----------------------------------------------------------------- private

    def _compute_needed(self, effect_kwargs: dict) -> set[str]:
        active = effect_kwargs.get("active_effects", config.ACTIVE_EFFECTS)
        needed: set[str] = set()
        for name in active:
            cls = EFFECTS.get(name)
            if cls and hasattr(cls, "needed_lights"):
                needed.update(cls.needed_lights(**effect_kwargs))
        return needed

    def _extend_rig(self, effect_kwargs: dict) -> None:
        """Connect any lights that effect_kwargs needs but aren't already in the rig."""
        if not self._rig:
            return
        needed = self._compute_needed(effect_kwargs)
        for name in needed:
            ctrl = self._rig.get(name)
            if ctrl is not None and ctrl.connected:
                continue
            cfg_entry = config.LIFX_LIGHTS.get(name, {})
            if not cfg_entry:
                continue
            if ctrl is None:
                ctrl = LIFXController(
                    ip=cfg_entry.get("ip"),
                    label=cfg_entry.get("label"),
                    discovery_timeout=cfg_entry.get("discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT),
                )
                self._rig.register(name, ctrl)
            ok = ctrl.connect()
            with self._lock:
                self._light_status[name] = "connected" if ok else "offline"

    def _run(self, effect_kwargs: dict) -> None:
        """Connect needed lights, save rig, then hand off to _poll_loop."""
        needed = self._compute_needed(effect_kwargs)

        rig = LightRig()
        for name, cfg_entry in config.LIFX_LIGHTS.items():
            if name not in needed:
                with self._lock:
                    self._light_status[name] = "unused"
                continue
            rig.register(name, LIFXController(
                ip=cfg_entry.get("ip"),
                label=cfg_entry.get("label"),
                discovery_timeout=cfg_entry.get("discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT),
            ))

        results = rig.connect_all()
        with self._lock:
            for name in config.LIFX_LIGHTS:
                if name not in needed:
                    self._light_status[name] = "unused"
                elif results.get(name):
                    self._light_status[name] = "connected"
                else:
                    self._light_status[name] = "offline"

        self._rig = rig  # expose before entering poll loop so pause() can grab it

        if needed and not results.get("strip"):
            logger.error("Strip failed to connect — engine stopping")
            return

        rig.power_on_all()
        self._poll_loop(effect_kwargs)

    def _poll_loop(self, effect_kwargs: dict) -> None:
        """Run effects loop. Extends rig with any missing lights first."""
        rig = self._rig
        if not rig:
            return

        self._extend_rig(effect_kwargs)

        strip = rig.get("strip")
        # Only abort if the strip was registered (needed) but failed to connect
        if strip is not None and not strip.connected:
            logger.error("Strip not available — cannot run poll loop")
            return

        if strip:
            strip.set_idle()

        simhub = SimHubClient(host=config.SIMHUB_HOST, port=config.SIMHUB_PORT)
        simhub.start()
        with self._lock:
            self._simhub_status = "waiting"

        active = effect_kwargs.get("active_effects", config.ACTIVE_EFFECTS)
        effects = []
        for name in active:
            cls = EFFECTS.get(name)
            if cls:
                effects.append(cls(rig, **effect_kwargs))

        idle = True
        last_active = 0.0
        while not self._stop_event.is_set():
            stale = simhub.seconds_since_last_packet() > config.SIMHUB_IDLE_TIMEOUT
            telemetry = simhub.get_data()

            with self._lock:
                self._simhub_status = "connected" if (not stale and telemetry) else "waiting"

            if stale or not telemetry:
                if not idle:
                    if strip:
                        strip.set_idle()
                    idle = True
            else:
                last_active = time.monotonic()
                with self._lock:
                    self._last_telemetry = dict(telemetry)
                if idle:
                    if strip:
                        strip.init_zone_pattern()
                    idle = False
                for effect in effects:
                    effect.update(telemetry)

            dormant = (time.monotonic() - last_active) > _DORMANT_AFTER
            time.sleep(_POLL_INTERVAL_DORMANT if dormant else _POLL_INTERVAL)

        simhub.stop()
        if strip:
            strip.set_idle()
