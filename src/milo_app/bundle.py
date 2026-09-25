"""Read a MILO sim bundle folder exactly as it is on disk.

Layout:
    <bundle_id>/
      INPUT/   inputs.json  + files/...
      OUTPUT/  outputs.json + files/...
      bundle.json

Reading never fails on content: anything that looks missing or odd is recorded
in `Bundle.problems` so it can be shown next to the ingested data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST = "bundle.json"
SECTORS = ("INPUT", "OUTPUT")
ITEMS_FILE = {"INPUT": "inputs.json", "OUTPUT": "outputs.json"}
# Fields every bundle.json is expected to carry (reported if missing, never blocking).
EXPECTED_MANIFEST_FIELDS = ("bundle_id", "product", "module", "task", "status", "start", "finish", "time_elapsed")


@dataclass
class Item:
    """One input or output value."""

    sector: str
    order: int
    name: str
    value: Any
    units: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BundleFile:
    sector: str  # INPUT, OUTPUT, or ROOT (a file outside both sectors)
    relpath: str  # POSIX path relative to the bundle root
    path: Path
    size: int
    sha256: str


@dataclass
class Bundle:
    root: Path
    bundle_id: str
    manifest: dict[str, Any]
    items: list[Item]
    files: list[BundleFile]
    problems: list[str]

    def items_in(self, sector: str) -> list[Item]:
        return [item for item in self.items if item.sector == sector]

    def files_in(self, sector: str) -> list[BundleFile]:
        return [f for f in self.files if f.sector == sector]


def is_bundle_dir(path: Path) -> bool:
    return path.is_dir() and (path / MANIFEST).is_file()


def _load_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        problems.append(f"missing {path.name}")
    except json.JSONDecodeError as exc:
        problems.append(f"{path.name} is not valid JSON: {exc}")
    return None


def parse_items(data: Any, sector: str, problems: list[str]) -> list[Item]:
    """Accept either {name: value | {value, units, ...}} or [{name, value, units, ...}, ...].

    A top-level wrapper like {"inputs": [...]} / {"outputs": [...]} / {"items": [...]} is unwrapped.
    """
    if data is None:
        return []
    if isinstance(data, dict):
        for wrapper in (sector.lower() + "s", "items"):
            if set(data) == {wrapper} and isinstance(data[wrapper], (list, dict)):
                data = data[wrapper]
                break

    items: list[Item] = []
    if isinstance(data, dict):
        for order, (name, raw) in enumerate(data.items()):
            if isinstance(raw, dict) and "value" in raw:
                extra = {k: v for k, v in raw.items() if k not in ("value", "units")}
                items.append(Item(sector, order, str(name), raw["value"], raw.get("units"), extra))
            else:
                items.append(Item(sector, order, str(name), raw))
    elif isinstance(data, list):
        for order, raw in enumerate(data):
            if not isinstance(raw, dict) or "name" not in raw:
                problems.append(f"{ITEMS_FILE[sector]} entry #{order} has no 'name'; stored as-is")
                items.append(Item(sector, order, f"unnamed_{order}", raw))
                continue
            extra = {k: v for k, v in raw.items() if k not in ("name", "value", "units")}
            if "value" not in raw:
                problems.append(f"{ITEMS_FILE[sector]} entry '{raw['name']}' has no 'value'")
            items.append(Item(sector, order, str(raw["name"]), raw.get("value"), raw.get("units"), extra))
    else:
        problems.append(f"{ITEMS_FILE[sector]} must be an object or a list; got {type(data).__name__}")
    return items


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_bundle(root: str | Path) -> Bundle:
    root = Path(root).resolve()
    problems: list[str] = []

    manifest = _load_json(root / MANIFEST, problems)
    if not isinstance(manifest, dict):
        if manifest is not None:
            problems.append("bundle.json must be a JSON object")
        manifest = {}
    for key in EXPECTED_MANIFEST_FIELDS:
        if key not in manifest:
            problems.append(f"bundle.json has no '{key}'")

    bundle_id = str(manifest.get("bundle_id") or root.name)
    if manifest.get("bundle_id") and manifest["bundle_id"] != root.name:
        problems.append(f"folder name '{root.name}' differs from bundle_id '{bundle_id}'")

    items: list[Item] = []
    for sector in SECTORS:
        sector_dir = root / sector
        if not sector_dir.is_dir():
            problems.append(f"missing {sector}/ folder")
            continue
        items_path = sector_dir / ITEMS_FILE[sector]
        items.extend(parse_items(_load_json(items_path, problems), sector, problems))

    data_files = {MANIFEST, "INPUT/inputs.json", "OUTPUT/outputs.json"}
    files: list[BundleFile] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if str(rel) in data_files:
            continue
        sector = rel.parts[0] if rel.parts[0] in SECTORS else "ROOT"
        if sector == "ROOT":
            problems.append(f"file outside INPUT/ and OUTPUT/: {rel}")
        files.append(BundleFile(sector, str(rel), path, path.stat().st_size, _sha256(path)))

    return Bundle(root, bundle_id, manifest, items, files, problems)


def find_bundles(path: str | Path, max_depth: int = 4) -> list[Path]:
    """Every bundle folder at or under `path`.

    Searches a few levels deep: copying a bundle from the VM often nests it
    (drop_dir/<id>/<id>/bundle.json). Never descends into a bundle it already found.
    """
    path = Path(path).resolve()
    if is_bundle_dir(path):
        return [path]
    if max_depth <= 0 or not path.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(path.iterdir()):
        if child.is_dir():
            found.extend(find_bundles(child, max_depth - 1))
    return found
