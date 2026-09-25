"""Docker and Neo4j, brought up in the background so the app never needs anything started by hand.

Docker Desktop on this PC has one recurring failure: a previous session leaves Windows socket
files behind (Docker\\run\\dockerInference, docker-secrets-engine\\engine.sock), and the next
start dies on "The file cannot be accessed by the system". Whenever Docker is fully down we move
those folders aside before starting it; Docker makes fresh ones.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from milo_app.config import COMPOSE_FILE, COMPOSE_PROJECT, DATA

DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DOCKER_PROCESSES = ("Docker Desktop", "com.docker.backend", "com.docker.build", "docker-desktop")
LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
SOCKET_FOLDERS = (LOCAL / "Docker" / "run", LOCAL / "docker-secrets-engine")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

Report = Callable[[str], None]


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def docker_ready() -> bool:
    try:
        done = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip()[:1].isdigit()


def _docker_running() -> bool:
    """Is any part of Docker Desktop alive (starting, running, or stuck on an error dialog)?"""
    try:
        listing = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=20).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(f'"{name.lower()}.exe"' in listing for name in DOCKER_PROCESSES)


def _stop_docker() -> None:
    for name in DOCKER_PROCESSES:
        _run(["taskkill", "/F", "/T", "/IM", name + ".exe"], timeout=20)
    time.sleep(3)


def _clear_stale_sockets() -> None:
    """Move the socket folders into data/docker-stale/<time>/. Moved, never deleted."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for folder in SOCKET_FOLDERS:
        if folder.exists():
            target = DATA / "docker-stale" / stamp / folder.name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(folder), str(target))
            except OSError:
                pass  # something still holds it; Docker's own error will say what


def _quiet_dashboard() -> None:
    """Ask Docker Desktop not to open its dashboard window. Only safe to write while Docker is down."""
    store = Path(os.environ.get("APPDATA", "")) / "Docker" / "settings-store.json"
    try:
        settings = json.loads(store.read_text(encoding="utf-8"))
        if settings.get("OpenUIOnStartupDisabled") is not True:
            settings["OpenUIOnStartupDisabled"] = True
            store.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _wait(check: Callable[[], bool], seconds: float, every: float = 3) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(every)
    return False


def ensure_docker(report: Report) -> None:
    if docker_ready():
        return
    if _docker_running():
        report("Waiting for Docker…")
        if _wait(docker_ready, 60):
            return
        report("Restarting Docker…")
        _stop_docker()

    if not DOCKER_DESKTOP.is_file():
        raise RuntimeError(f"Docker Desktop is not installed at {DOCKER_DESKTOP}")
    report("Starting Docker…")
    _clear_stale_sockets()
    _quiet_dashboard()
    # -Autostart is the flag Windows' own login entry uses: tray icon, no window.
    subprocess.Popen([str(DOCKER_DESKTOP), "-Autostart"], creationflags=NO_WINDOW)
    if not _wait(docker_ready, 240, every=4):
        raise RuntimeError("Docker Desktop did not start within 4 minutes. Open it to see what it says.")


def ensure_neo4j(report: Report, is_ready: Callable[[], bool]) -> None:
    if is_ready():
        return
    report("Starting the graph database…")
    done = _run(["docker", "compose", "-p", COMPOSE_PROJECT, "-f", str(COMPOSE_FILE), "up", "-d"], timeout=600)
    if done.returncode != 0:
        raise RuntimeError("docker compose up failed:\n" + (done.stderr or done.stdout).strip())
    report("Waiting for the graph database…")
    if not _wait(is_ready, 180, every=2):
        raise RuntimeError("Neo4j started but did not answer within 3 minutes.")


def start_all(report: Report, neo4j_ready: Callable[[], bool]) -> None:
    ensure_docker(report)
    ensure_neo4j(report, neo4j_ready)
