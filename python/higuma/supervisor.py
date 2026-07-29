from __future__ import annotations

import signal
import socket

# Workers use sys.executable with a fixed argv and never enable a shell.
import subprocess  # nosec B404
import sys
import time
from itertools import cycle
from threading import Event, Thread
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
    ) -> None:
        if processes < 2:
            raise ValueError("Supervisor requires at least two processes")
        self.app = app
        self.host = host
        self.port = port
        self.processes = processes
        self.threads = threads
        self.startup_timeout = startup_timeout
        self._children: list[subprocess.Popen[Any]] = []
        self._stopped = Event()

    def run(self) -> None:
        internal_ports = [_free_port() for _ in range(self.processes)]
        self._children = [self._start_worker(port) for port in internal_ports]
        try:
            for port in internal_ports:
                _wait_for_port("127.0.0.1", port, self.startup_timeout)
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
        targets = cycle(internal_ports)
        listener = socket.create_server((self.host, self.port), reuse_port=False)
        listener.settimeout(0.5)
        print(
            f"higuma supervisor listening on http://{self.host}:{self.port} "
            f"with {self.processes} processes"
        )
        try:
            while not self._stopped.is_set():
                failed = next(
                    (child for child in self._children if child.poll() is not None),
                    None,
                )
                if failed is not None:
                    raise RuntimeError(
                        f"higuma worker exited unexpectedly with {failed.returncode}"
                    )
                try:
                    client, _ = listener.accept()
                except TimeoutError:
                    continue
                target_port = next(targets)
                Thread(
                    target=_proxy_connection,
                    args=(client, ("127.0.0.1", target_port)),
                    daemon=True,
                ).start()
        finally:
            listener.close()
            _restore_signal_handlers(previous_handlers)


def _proxy_connection(client: socket.socket, target: tuple[str, int]) -> None:
    try:
        upstream = socket.create_connection(target, timeout=10)
    except OSError:
        client.close()
        return
    client.settimeout(None)
    upstream.settimeout(None)
    first = Thread(target=_copy_socket, args=(client, upstream), daemon=True)
    second = Thread(target=_copy_socket, args=(upstream, client), daemon=True)
    first.start()
    second.start()
    first.join()
    second.join()
    client.close()
    upstream.close()


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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
