import logging
import signal
import sys
import time

import config
import log_setup
from effects import EFFECTS
from lifx_controller import LIFXController
from light_rig import LightRig
from simhub_client import SimHubClient

log_setup.setup()
logger = logging.getLogger(__name__)

POLL_HZ = 20
POLL_INTERVAL = 1 / POLL_HZ


def _build_rig(needed: set[str]) -> LightRig:
    rig = LightRig()
    for name, cfg in config.LIFX_LIGHTS.items():
        if name not in needed:
            continue
        rig.register(
            name,
            LIFXController(
                ip=cfg.get("ip"),
                label=cfg.get("label"),
                discovery_timeout=cfg.get("discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT),
            ),
        )
    return rig


def main() -> None:
    effect_kwargs = {
        "start_rpm":          config.REV_START_RPM,
        "start_threshold":    config.REV_START_THRESHOLD,
        "redline_threshold":  config.REV_REDLINE_THRESHOLD,
        "flash_interval":     config.REV_FLASH_INTERVAL,
        "transition_ms":      config.LIFX_TRANSITION_MS,
        "led_step":           config.LED_STEP,
        "counter_mode":       config.COUNTER_MODE,
        "strip_reversed":     config.STRIP_REVERSED,
        "strip_max_brightness": config.STRIP_MAX_BRIGHTNESS,
        "brake_lights":          config.BRAKE_LIGHTS,
        "brake_threshold":       config.BRAKE_THRESHOLD,
        "brake_max_brightness":  config.BRAKE_MAX_BRIGHTNESS,
    }

    needed: set[str] = set()
    for name in config.ACTIVE_EFFECTS:
        cls = EFFECTS.get(name)
        if cls and hasattr(cls, "needed_lights"):
            needed.update(cls.needed_lights(**effect_kwargs))
    logger.info("Lights needed by active effects: %s", sorted(needed))

    rig = _build_rig(needed)
    results = rig.connect_all()

    if not results.get("strip"):
        logger.error("LED strip failed to connect — cannot continue.")
        sys.exit(1)

    rig.power_on_all()
    rig.get("strip").set_idle()

    simhub = SimHubClient(host=config.SIMHUB_HOST, port=config.SIMHUB_PORT)
    simhub.start()

    effects = []
    for name in config.ACTIVE_EFFECTS:
        cls = EFFECTS.get(name)
        if cls is None:
            logger.warning("Unknown effect '%s' — skipping", name)
            continue
        effects.append(cls(rig, **effect_kwargs))
        logger.info("Effect loaded: %s", name)

    def _shutdown(*_):
        logger.info("Shutting down...")
        simhub.stop()
        rig.get("strip").set_idle()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Running %d effect(s) at %dHz. Waiting for SimHub...", len(effects), POLL_HZ)
    idle = True

    while True:
        stale = simhub.seconds_since_last_packet() > config.SIMHUB_IDLE_TIMEOUT
        telemetry = simhub.get_data()

        if stale or not telemetry:
            if not idle:
                logger.info("No SimHub data — going idle.")
                rig.get("strip").set_idle()
                idle = True
        else:
            if idle:
                logger.info("SimHub data received — effects active.")
                rig.get("strip").init_zone_pattern()
                idle = False
            for effect in effects:
                effect.update(telemetry)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
