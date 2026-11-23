from __future__ import annotations

import math
import signal
import socket

# Workers use sys.executable with a fixed argv and never enable a shell.
import subprocess  # nosec B404
import sys
import time
from collections import deque
from ipaddress import ip_address
from itertools import cycle
from threading import BoundedSemaphore, Event, Thread
from typing import Any


class Supervisor:
    def __init__(
        self,
        app: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        processes: int = 2,
        threads: int = 0,
        startup_timeout: float = 15.0,
        max_restarts: int = 5,
        restart_window: float = 60.0,
        max_connections: int = 1024,
    ) -> None:
        if not isinstance(app, str) or not app.strip() or app.strip().startswith("-"):
            raise ValueError("app must be a non-empty module:object reference")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host must be a non-empty string")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        if isinstance(processes, bool) or not isinstance(processes, int) or processes < 2:
            raise ValueError("Supervisor requires at least two processes")
        if (
            isinstance(threads, bool)
            or not isinstance(threads, int)
            or threads < 0
            or isinstance(startup_timeout, bool)
            or not isinstance(startup_timeout, (int, float))
            or not math.isfinite(startup_timeout)
            or startup_timeout <= 0
        ):
            raise ValueError("threads must be non-negative and startup_timeout must be positive")
        self.app = app.strip()
        self.host = host
        self.port = port
        self.processes = processes
        self.threads = threads
        self.startup_timeout = startup_timeout
        if (
            isinstance(max_restarts, bool)
            or not isinstance(max_restarts, int)
            or max_restarts < 0
            or isinstance(restart_window, bool)
            or not isinstance(restart_window, (int, float))
            or not math.isfinite(restart_window)
            or restart_window <= 0
            or isinstance(max_connections, bool)
            or not isinstance(max_connections, int)
            or max_connections <= 0
        ):
            raise ValueError(
                "max_restarts must be non-negative; restart_window and max_connections "
                "must be positive"
            )
        self.max_restarts = max_restarts
        self.restart_window = restart_window
        self.max_connections = max_connections
        self._children: list[subprocess.Popen[Any]] = []
        self._restart_times: deque[float] = deque()
        self._stopped = Event()

    def run(self) -> None:
        internal_ports = _free_ports(self.processes)
        self._children = [self._start_worker(port) for port in internal_ports]
        try:
            for child, port in zip(self._children, internal_ports):
                _wait_for_port("127.0.0.1", port, self.startup_timeout, child)
            self._serve(internal_ports)
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        for child in self._children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 5
        for child in self._children:
            if child.poll() is not None:
                continue
            try:
                child.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()

    def _start_worker(self, port: int) -> subprocess.Popen[Any]:
        return subprocess.Popen(  # nosec B603
            [
                sys.executable,
                "-m",
                "higuma",
                "run",
                self.app,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                str(self.threads),
                "--internal-worker",
            ]
        )

    def _serve(self, internal_ports: list[int]) -> None:
        previous_handlers = _install_signal_handlers(self.stop)
        listener: socket.socket | None = None
        try:
            targets = cycle(internal_ports)
            connection_slots = BoundedSemaphore(self.max_connections)
            bind_host, family = _bind_target(self.host)
            listener = socket.create_server((bind_host, self.port), family=family, reuse_port=False)
            listener.settimeout(0.5)
            bound_port = listener.getsockname()[1]
            display_host = f"[{bind_host}]" if ":" in bind_host else bind_host
            print(
                f"higuma supervisor listening on http://{display_host}:{bound_port} "
                f"with {self.processes} processes"
            )
            while not self._stopped.is_set():
                failed = next(
                    (
                        (index, child)
                        for index, child in enumerate(self._children)
                        if child.poll() is not None
                    ),
                    None,
                )
                if failed is not None:
                    index, child = failed
                    self._restart_worker(index, internal_ports[index], child.returncode)
                try:
                    client, _ = listener.accept()
                except TimeoutError:
                    continue
                if not connection_slots.acquire(blocking=False):
                    client.close()
                    continue
                target_port = next(targets)
                try:
                    Thread(
                        target=_proxy_connection,
                        args=(client, ("127.0.0.1", target_port), connection_slots),
                        daemon=True,
                    ).start()
                except BaseException:
                    client.close()
                    connection_slots.release()
                    raise
        finally:
            if listener is not None:
                listener.close()
            _restore_signal_handlers(previous_handlers)

    def _restart_worker(self, index: int, port: int, returncode: int | None) -> None:
        now = time.monotonic()
        while self._restart_times and self._restart_times[0] <= now - self.restart_window:
            self._restart_times.popleft()
        if len(self._restart_times) >= self.max_restarts:
            raise RuntimeError(f"higuma worker restart limit exceeded after exit {returncode}")
        self._restart_times.append(now)
        replacement = self._start_worker(port)
        self._children[index] = replacement
        _wait_for_port("127.0.0.1", port, self.startup_timeout, replacement)


def _proxy_connection(
    client: socket.socket,
    target: tuple[str, int],
    connection_slots: BoundedSemaphore | None = None,
) -> None:
    try:
        try:
            upstream = socket.create_connection(target, timeout=10)
        except OSError:
            return
        try:
            client.settimeout(None)
            upstream.settimeout(None)
            first = Thread(target=_copy_socket, args=(client, upstream), daemon=True)
            second = Thread(target=_copy_socket, args=(upstream, client), daemon=True)
            first.start()
            second.start()
            first.join()
            second.join()
        finally:
            upstream.close()
    finally:
        client.close()
        if connection_slots is not None:
            connection_slots.release()


def _copy_socket(source: socket.socket, destination: socket.socket) -> None:
    try:
        while chunk := source.recv(64 * 1024):
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _bind_target(host: str) -> tuple[str, int]:
    cleaned = host.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    try:
        family = socket.AF_INET6 if ip_address(cleaned).version == 6 else socket.AF_INET
    except ValueError:
        family = socket.AF_INET
    return cleaned, family


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _free_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
            ports.append(int(sock.getsockname()[1]))
        return ports
    finally:
        for sock in sockets:
            sock.close()


def _wait_for_port(
    host: str,
    port: int,
    timeout: float,
    child: subprocess.Popen[Any] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child is not None and child.poll() is not None:
            raise RuntimeError(f"higuma worker exited during startup with {child.returncode}")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"higuma worker on {host}:{port} did not start")


def _install_signal_handlers(callback: Any) -> dict[int, Any]:
    handlers = {}
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        handlers[signal_number] = signal.getsignal(signal_number)
        signal.signal(signal_number, lambda *_: callback())
    return handlers


def _restore_signal_handlers(handlers: dict[int, Any]) -> None:
    for signal_number, handler in handlers.items():
        signal.signal(signal_number, handler)
