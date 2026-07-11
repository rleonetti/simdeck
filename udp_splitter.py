"""
UDP Splitter — receives telemetry on one port and fans it out to multiple destinations.

Standalone:  python udp_splitter.py
As library:  from udp_splitter import UDPSplitter
"""

import socket
import threading
import time

LISTEN_PORT = 20777
TARGETS = [
    ("127.0.0.1", 20066),  # Moza Pit House
    ("127.0.0.1", 8000),   # SimHub
]


class UDPSplitter:
    """Receives UDP on listen_port and re-sends each packet to all targets."""

    def __init__(self, listen_port: int, targets: list[tuple[str, int]]) -> None:
        self._listen_port = listen_port
        self._targets: list[tuple[str, int]] = list(targets)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._packet_count = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def listen_port(self) -> int:
        return self._listen_port

    @listen_port.setter
    def listen_port(self, port: int) -> None:
        self._listen_port = port

    @property
    def packet_count(self) -> int:
        with self._lock:
            return self._packet_count

    def get_targets(self) -> list[tuple[str, int]]:
        with self._lock:
            return list(self._targets)

    def set_targets(self, targets: list[tuple[str, int]]) -> None:
        with self._lock:
            self._targets = list(targets)

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        with self._lock:
            self._packet_count = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="udp-splitter")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv.settimeout(0.5)
        try:
            recv.bind(("127.0.0.1", self._listen_port))
        except OSError as exc:
            print(f"UDP splitter: bind failed on port {self._listen_port}: {exc}")
            return

        send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            while not self._stop_event.is_set():
                try:
                    data, _ = recv.recvfrom(65535)
                except socket.timeout:
                    continue
                with self._lock:
                    targets = list(self._targets)
                for target in targets:
                    try:
                        send.sendto(data, target)
                    except OSError:
                        pass
                with self._lock:
                    self._packet_count += 1
        finally:
            recv.close()
            send.close()


def main() -> None:
    splitter = UDPSplitter(LISTEN_PORT, TARGETS)
    splitter.start()
    print(f"UDP splitter listening on port {LISTEN_PORT}")
    print(f"Forwarding to: {', '.join(f'{ip}:{port}' for ip, port in TARGETS)}")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(5)
            count = splitter.packet_count
            if count:
                print(f"  {count:,} packets forwarded")
    except KeyboardInterrupt:
        print(f"\nStopped. {splitter.packet_count:,} packets forwarded total.")
        splitter.stop()


if __name__ == "__main__":
    main()
