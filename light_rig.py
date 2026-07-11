import logging

from lifx_controller import LIFXController

logger = logging.getLogger(__name__)


class LightRig:
    """
    Registry of named LIFX controllers.

    Build once at startup, pass to every effect. Effects call rig.get("name")
    to get the controller they need. Missing or failed lights return None and
    all LIFXController methods are no-ops when the underlying device is absent,
    so effects don't need to guard against it.

    Usage:
        rig = LightRig()
        rig.register("strip",       LIFXController(ip="..."))
        rig.register("front_right", LIFXController(ip="..."))
        rig.connect_all()
        rig.power_on_all()
    """

    def __init__(self) -> None:
        self._lights: dict[str, LIFXController] = {}

    def register(self, name: str, controller: LIFXController) -> None:
        self._lights[name] = controller

    def get(self, name: str) -> LIFXController | None:
        return self._lights.get(name)

    def connect_all(self) -> dict[str, bool]:
        """Connect every registered light. Returns {name: success} for each."""
        results: dict[str, bool] = {}
        for name, ctrl in self._lights.items():
            ok = ctrl.connect()
            results[name] = ok
            if ok:
                logger.success("%-15s connected", name)
            else:
                logger.warning("%-15s FAILED to connect — effects using it will be skipped", name)
        return results

    def power_on_all(self) -> None:
        for ctrl in self._lights.values():
            ctrl.power_on()

    def set_idle_all(self) -> None:
        for ctrl in self._lights.values():
            ctrl.set_idle()
