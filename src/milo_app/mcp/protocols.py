"""Discovery Studio protocols as the real VM describes them.

Every ds_protocol run dumps its protocol's own parameter list (name, type, default) into
INPUT/files/settings/milo_parameters_run_defaults.tsv before it changes anything, so even a run that fails
leaves it behind. MILO keeps the newest one per protocol in data/protocols/ (it survives the run's folder
being deleted), and from then on every script for that protocol is checked against the real names and
types: when it is written (validate) and when it runs against the fake DiscoveryScript (the checker loads
these lists). A protocol that has never run on the VM cannot be checked, and says so.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from milo_app.config import get_settings

DEFAULTS_DUMP = Path("INPUT", "files", "settings", "milo_parameters_run_defaults.tsv")


def store() -> Path:
    return get_settings().data_dir / "protocols"


def _file_for(name: str) -> Path:
    return store() / (re.sub(r"[^\w.-]+", "_", name).strip("_") + ".tsv")


def _header(path: Path) -> dict[str, str]:
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("# "):
            break
        key, _, value = line[2:].partition(": ")
        found[key] = value
    return found


def learn() -> dict[str, str]:
    """Keep the newest real parameter dump of every Discovery Studio protocol found in the drop folder.
    Returns {protocol: bundle_id} for the ones learned now."""
    learned: dict[str, str] = {}
    drop = get_settings().drop_dir
    if not drop.is_dir():
        return learned
    for manifest in [*drop.glob("*/bundle.json"), *drop.glob("*/*/bundle.json")]:
        dump = manifest.parent / DEFAULTS_DUMP
        if not dump.is_file():
            continue
        try:
            bundle = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = bundle.get("module")
        if bundle.get("product") != "Discovery Studio" or not name:
            continue
        finish = str(bundle.get("finish") or bundle.get("start") or "")
        target = _file_for(name)
        if target.is_file() and _header(target).get("finish", "") >= finish:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# protocol: {name}\n# from: {bundle.get('bundle_id')}\n# finish: {finish}\n"
                          + dump.read_text(encoding="utf-8"), encoding="utf-8")
        learned[name] = str(bundle.get("bundle_id"))
    return learned


def known() -> dict[str, dict[str, Any]]:
    """{protocol: {"from": bundle_id, "parameters": {name: {"type", "default"}}}} for every protocol that has
    run on the VM."""
    learn()
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(store().glob("*.tsv")) if store().is_dir() else []:
        head = _header(path)
        parameters = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# ") or line.startswith("name\ttype\t") or not line.strip():
                continue
            name, _, rest = line.partition("\t")
            kind, _, default = rest.partition("\t")
            parameters[name] = {"type": kind, "default": default}
        if head.get("protocol") and parameters:
            found[head["protocol"]] = {"from": head.get("from"), "parameters": parameters}
    return found


def _fits(kind: str, value: Any) -> bool:
    if isinstance(value, (list, tuple, dict)):
        return kind not in ("BoolType", "DoubleType", "LongType")
    text = str(value).strip()
    if kind == "BoolType":
        return isinstance(value, bool) or text.lower() in ("true", "false", "1", "0")
    if kind in ("DoubleType", "LongType"):
        if isinstance(value, bool):
            return False
        try:
            number = float(text)
        except ValueError:
            return False
        return kind == "DoubleType" or number == int(number)
    return True


def validate(protocol: str, parameters: list[list[Any]], structure_parameter: str | None) -> list[str]:
    """Check a ds_protocol's requested parameters against the protocol's real list. Raises ValueError naming
    every wrong one; returns warnings (the protocol has never run on the VM, so nothing could be checked)."""
    protocols = known()
    if protocol not in protocols:
        near = difflib.get_close_matches(protocol, list(protocols), n=1)
        return [f"Discovery Studio protocol '{protocol}' has never run on the VM, so its parameter names and types "
                "cannot be checked yet: its first run is unverified. That run records the protocol's real parameter "
                "list even if it fails, and every later script is checked against it."
                + (f" (A protocol MILO does know: '{near[0]}'.)" if near else "")]
    real = protocols[protocol]["parameters"]
    errors = []
    wanted = [(name, value) for name, value, _units in parameters]
    if structure_parameter:
        wanted.append((structure_parameter, None))
    for name, value in wanted:
        if name not in real:
            near = difflib.get_close_matches(name, list(real), n=3, cutoff=0.5)
            errors.append(f"'{protocol}' has no parameter '{name}'"
                          + (f" (did you mean {', '.join(repr(n) for n in near)}?)" if near else ""))
            continue
        kind = real[name]["type"]
        if kind == "GroupType":
            errors.append(f"'{name}' is a group heading in '{protocol}', not a setting")
        elif value is not None and not _fits(kind, value):
            errors.append(f"'{name}' is a {kind} in '{protocol}' (default {real[name]['default']!r}); "
                          f"{value!r} is not one")
    if errors:
        raise ValueError(f"Parameters that the real '{protocol}' would refuse (its list as the VM gave it, run "
                         f"{protocols[protocol]['from']}):\n- " + "\n- ".join(errors))
    return []
