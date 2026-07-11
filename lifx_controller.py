import logging
import sys

from lifxlan import LifxLAN

logger = logging.getLogger(__name__)


def _patch_windows_udp() -> None:
    # Python's socket.ioctl does NOT support SIO_UDP_CONNRESET, so we call
    # WSAIoctl directly via ctypes. We patch LifxLAN.initialize_socket so the
    # ioctl runs immediately after the socket is created, before any recvfrom.
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wt
        import lifxlan.lifxlan as _mod

        _SIO_UDP_CONNRESET = 0x9800000C
        _orig = _mod.LifxLAN.initialize_socket

        def _patched(self, timeout):
            _orig(self, timeout)
            try:
                flag = wt.BOOL(False)
                returned = wt.DWORD(0)
                ctypes.windll.ws2_32.WSAIoctl(
                    self.sock.fileno(),
                    _SIO_UDP_CONNRESET,
                    ctypes.byref(flag), ctypes.sizeof(flag),
                    None, 0,
                    ctypes.byref(returned),
                    None, None,
                )
            except Exception as exc:
                logger.debug("WSAIoctl SIO_UDP_CONNRESET failed: %s", exc)

        _mod.LifxLAN.initialize_socket = _patched


_patch_windows_udp()

# LIFX HSBK scale
HSBK_MAX = 65535
IDLE_COLOR = [0, 0, HSBK_MAX // 8, 3000]  # very dim warm white


class LIFXController:
    """Wraps a single LIFX light for sim racing effects."""

    def __init__(self, ip: str | None = None, label: str | None = None, discovery_timeout: int = 5):
        self.ip = ip
        self.label = label
        self.discovery_timeout = discovery_timeout
        self._light = None
        self._is_multizone = False
        self._num_zones = 0

    @property
    def num_zones(self) -> int:
        return self._num_zones

    @property
    def connected(self) -> bool:
        return self._light is not None

    def connect(self) -> bool:
        if self.ip:
            ok = self._connect_by_ip(self.ip)
        else:
            ok = self._connect_by_broadcast()
        if ok:
            self._detect_multizone()
        return ok

    def _connect_by_ip(self, ip: str) -> bool:
        """Send discovery unicast to a specific IP — works across VLANs."""
        import lifxlan.lifxlan as _mod
        logger.info("Connecting directly to LIFX device at %s...", ip)
        orig = _mod.UDP_BROADCAST_IP_ADDRS
        _mod.UDP_BROADCAST_IP_ADDRS = [ip]
        try:
            lan = LifxLAN()
            devices = lan.get_devices()
        finally:
            _mod.UDP_BROADCAST_IP_ADDRS = orig

        if not devices:
            logger.error("No LIFX device responded at %s", ip)
            return False
        self._light = devices[0]
        logger.success("Connected to: %s", self._light.get_label())
        return True

    def _connect_by_broadcast(self) -> bool:
        logger.info("Discovering LIFX devices via broadcast (timeout=%ds)...", self.discovery_timeout)
        lan = LifxLAN()
        if self.label:
            devices = lan.get_devices_by_name(self.label)
            if not devices:
                logger.error("No LIFX device found with label '%s'", self.label)
                return False
        else:
            devices = lan.get_devices()
            if not devices:
                logger.error("No LIFX devices found on the network")
                return False
        self._light = devices[0]
        logger.success("Connected to: %s", self._light.get_label())
        return True

    def _detect_multizone(self) -> None:
        try:
            from lifxlan import MultiZoneLight
            if isinstance(self._light, MultiZoneLight):
                zones = self._light.get_color_zones(0, 255)
                self._num_zones = len(zones)
                self._is_multizone = True
                logger.success("Multi-zone strip detected: %d zones", self._num_zones)
            else:
                logger.info("Single-zone device — using brightness scaling for LED_STEP")
        except Exception as exc:
            logger.debug("Multi-zone detection failed: %s", exc)

    def init_zone_pattern(self) -> None:
        """No-op — kept for API compatibility."""

    def set_color(
        self,
        hue: int,
        saturation: int,
        brightness: int,
        kelvin: int = 3500,
        duration_ms: int = 50,
    ) -> None:
        """Set whole-strip color. All HSBK values 0-65535, kelvin 2500-9000."""
        if not self._light:
            return
        try:
            self._light.set_color(
                [hue, saturation, brightness, kelvin],
                duration_ms,
                rapid=True,
            )
        except Exception as exc:
            logger.debug("LIFX set_color failed: %s", exc)

    def set_color_zoned(
        self,
        hue: int,
        saturation: int,
        brightness: int,
        kelvin: int = 3500,
        duration_ms: int = 50,
        step: int = 1,
    ) -> None:
        """Whole-strip color with brightness scaled by 1/step."""
        scaled = int(brightness / step) if step > 1 else brightness
        self.set_color(hue, saturation, scaled, kelvin, duration_ms)

    def set_zone_range(
        self,
        start: int,
        end: int,
        hue: int,
        saturation: int,
        brightness: int,
        kelvin: int = 3500,
        duration_ms: int = 0,
    ) -> None:
        """Set a contiguous range of zones to one color. No-op on single-zone devices."""
        if not self._is_multizone or not self._light or start > end:
            return
        try:
            self._light.set_zone_color(start, end, [hue, saturation, brightness, kelvin], duration_ms, rapid=True)
        except Exception as exc:
            logger.debug("LIFX set_zone_color failed: %s", exc)

    def set_idle(self) -> None:
        """Dim warm white when no game data is incoming."""
        if not self._light:
            return
        try:
            self._light.set_color(IDLE_COLOR, 500, rapid=False)
        except Exception:
            pass

    def power_on(self) -> None:
        if self._light:
            try:
                self._light.set_power(HSBK_MAX)
            except Exception:
                pass

    def power_off(self) -> None:
        if self._light:
            try:
                self._light.set_power(0)
            except Exception:
                pass
