from __future__ import annotations

import argparse
import http.client
import os
import socket
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _single_wheel(directory: Path) -> Path:
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel in {directory}, found {len(wheels)}")
    return wheels[0].resolve()


def _smoke_wheel_in_isolation(directory: Path, expected_version: str | None) -> None:
    wheel = _single_wheel(directory)
    with tempfile.TemporaryDirectory(prefix="higuma-smoke-") as temporary_directory:
        environment_path = Path(temporary_directory) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment_path)
        python = environment_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = os.environ.copy()
        environment["PYTHONNOUSERSITE"] = "1"
        environment.pop("PYTHONPATH", None)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--disable-pip-version-check",
                str(wheel),
            ],
            check=True,
            cwd=temporary_directory,
            env=environment,
        )
        command = [str(python), str(Path(__file__).resolve())]
        if expected_version:
            command.extend(("--expected-version", expected_version))
        subprocess.run(command, check=True, cwd=temporary_directory, env=environment)


def _wait_for_server(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(f"server exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            connection.close()
            if response.status == 200 and body == b'{"status":"ok"}':
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise TimeoutError("higuma smoke server did not become ready")


def _exercise_http(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("HEAD", "/health")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b""
    assert response.getheader("content-length") == "15"
    connection.close()

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/values/42")
    response = connection.getresponse()
    assert response.status == 200, response.status
    assert response.read() == b'{"value":42,"typed":true}'
    connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an installed higuma package")
    parser.add_argument("--wheel-dir", type=Path, help="install the single wheel from this folder")
    parser.add_argument("--expected-version")
    args = parser.parse_args()

    if args.wheel_dir:
        _smoke_wheel_in_isolation(args.wheel_dir, args.expected_version)
        return 0

    import higuma

    if args.expected_version and higuma.__version__ != args.expected_version:
        raise RuntimeError(
            f"installed version {higuma.__version__} does not match {args.expected_version}"
        )

    cli = subprocess.run(
        [sys.executable, "-m", "higuma", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    if f"higuma {higuma.__version__}" not in cli.stdout:
        raise RuntimeError(f"unexpected CLI version output: {cli.stdout!r}")

    port = _free_port()
    application = """
import os
from higuma import Higuma

app = Higuma(__name__, static_folder=None)

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/values/<int:value>')
def value(value: int):
    return {'value': value, 'typed': isinstance(value, int)}

app.run(host='127.0.0.1', port=int(os.environ['HIGUMA_SMOKE_PORT']), workers=1)
"""
    environment = os.environ.copy()
    environment["HIGUMA_SMOKE_PORT"] = str(port)
    process = subprocess.Popen(
        [sys.executable, "-c", application],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(process, port)
        _exercise_http(port)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    print(f"higuma {higuma.__version__} package smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
