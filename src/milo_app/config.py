"""Settings, read from the environment with .env (next to pyproject.toml) filling the gaps."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
COMPOSE_FILE = ROOT / "compose.yaml"
COMPOSE_PROJECT = "milo-app"


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    drop_dir: Path
    scan_seconds: float
    ghosts: int  # calculated ghosts per real run, for each knob (ghost.md 4.3)


def get_settings() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        neo4j_uri=env("MILO_NEO4J_URI", "bolt://localhost:7688"),
        neo4j_user=env("MILO_NEO4J_USER", "neo4j"),
        neo4j_password=env("MILO_NEO4J_PASSWORD", "milo-app-password"),
        drop_dir=Path(env("MILO_DROP_DIR", r"C:\milo_drop")),
        scan_seconds=float(env("MILO_SCAN_SECONDS", "3")),
        ghosts=int(env("MILO_GHOSTS", "1")),
    )
