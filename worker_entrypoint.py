#!/usr/bin/env python3
"""Dedicated App Service entrypoint for background upload/export workers."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Iterable, Sequence

from config import (
    EXPORT_WORKER_BATCH_SIZE,
    EXPORT_WORKER_POLL_SECONDS,
    UPLOAD_WORKER_BATCH_SIZE,
    UPLOAD_WORKER_POLL_SECONDS,
)
from worker_health_server import serve


APP_ROOT = Path(os.getenv("APP_PATH") or Path(__file__).resolve().parent)
LOG_DIR = Path(os.getenv("WORKER_LOG_DIR") or "/home/LogFiles")
WORKER_MODE = (os.getenv("WORKER_MODE") or "both").strip().lower()
RESTART_DELAY_SECONDS = float(os.getenv("WORKER_RESTART_DELAY_SECONDS") or "2.0")


def _ensure_runtime_venv() -> None:
    venv_python = APP_ROOT / "antenv" / "bin" / "python"
    venv_archive = APP_ROOT / "antenv.tar.gz"
    if venv_python.exists() or not venv_archive.exists():
        return
    print("[worker-entrypoint] extracting antenv.tar.gz", flush=True)
    with tarfile.open(venv_archive, "r:gz") as archive:
        archive.extractall(APP_ROOT)


def _runtime_site_packages() -> str | None:
    venv_lib = APP_ROOT / "antenv" / "lib"
    if not venv_lib.exists():
        return None
    candidates = sorted(venv_lib.glob("python*/site-packages"))
    if not candidates:
        return None
    return str(candidates[-1])


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    python_paths: list[str] = []
    site_packages = _runtime_site_packages()
    if site_packages:
        python_paths.append(site_packages)
        env["VIRTUAL_ENV"] = str(APP_ROOT / "antenv")
        env["PATH"] = f"{APP_ROOT / 'antenv' / 'bin'}:{env.get('PATH', '')}"
    existing_pythonpath = env.get("PYTHONPATH")
    python_paths.append(str(APP_ROOT))
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(path for path in python_paths if path)
    return env


def _python_bin() -> str:
    _ensure_runtime_venv()
    candidates = [
        "/opt/python/3.12.12/bin/python",
        shutil.which("python3") or "",
        shutil.which("python") or "",
        sys.executable or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "python3"


def _command(script_name: str, *, batch_size: str, poll_seconds: str) -> list[str]:
    return [
        _python_bin(),
        script_name,
        "--batch-size",
        batch_size,
        "--poll-seconds",
        poll_seconds,
    ]


def _worker_specs() -> list[tuple[str, Path, list[str]]]:
    specs: list[tuple[str, Path, list[str]]] = []
    if WORKER_MODE in {"upload", "both"}:
        specs.append(
            (
                "upload-worker",
                LOG_DIR / "upload-worker.log",
                _command(
                    "upload_worker.py",
                    batch_size=os.getenv("UPLOAD_WORKER_BATCH_SIZE", str(UPLOAD_WORKER_BATCH_SIZE)),
                    poll_seconds=os.getenv("UPLOAD_WORKER_POLL_SECONDS", str(UPLOAD_WORKER_POLL_SECONDS)),
                ),
            )
        )
    if WORKER_MODE in {"export", "both"}:
        specs.append(
            (
                "export-worker",
                LOG_DIR / "export-worker.log",
                _command(
                    "export_worker.py",
                    batch_size=os.getenv("EXPORT_WORKER_BATCH_SIZE", str(EXPORT_WORKER_BATCH_SIZE)),
                    poll_seconds=os.getenv("EXPORT_WORKER_POLL_SECONDS", str(EXPORT_WORKER_POLL_SECONDS)),
                ),
            )
        )
    if not specs:
        raise RuntimeError(f"Invalid WORKER_MODE={WORKER_MODE!r}; expected upload, export or both")
    return specs


def _supervise(name: str, log_path: Path, command: Sequence[str], stop_event: threading.Event) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    while not stop_event.is_set():
        with log_path.open("a", encoding="utf-8") as log_file:
            print(f"[worker-entrypoint] starting {name}: {' '.join(command)}", flush=True)
            log_file.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [{name}] starting\n")
            log_file.flush()
            proc = subprocess.Popen(
                list(command),
                cwd=str(APP_ROOT),
                env=_base_env(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            while proc.poll() is None and not stop_event.is_set():
                time.sleep(0.5)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                return
            exit_code = int(proc.returncode or 0)
            log_file.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                f"[{name}] exited with code {exit_code}; restarting in {RESTART_DELAY_SECONDS:.1f}s\n"
            )
            log_file.flush()
            print(f"[worker-entrypoint] {name} exited with code {exit_code}", flush=True)
        if stop_event.wait(RESTART_DELAY_SECONDS):
            return


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle_signal(signum: int, _frame) -> None:
        print(f"[worker-entrypoint] received signal {signum}, shutting down", flush=True)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def main() -> None:
    os.chdir(APP_ROOT)
    os.environ.update(_base_env())
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    for name, log_path, command in _worker_specs():
        thread = threading.Thread(
            target=_supervise,
            args=(name, log_path, command, stop_event),
            name=f"{name}-supervisor",
            daemon=True,
        )
        thread.start()

    port = int(os.getenv("PORT") or os.getenv("WEBSITES_PORT") or "8000")
    print(f"[worker-entrypoint] health host listening on {port}", flush=True)
    serve(port=port)


if __name__ == "__main__":
    main()
