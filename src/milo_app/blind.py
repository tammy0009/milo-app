"""Blind tests: every new run is predicted before it joins the data, then checked (ghost.md 6, 7.1).

When a new bundle arrives, before it is ingested:

  1. the models are fitted to the runs already in the graph (the same calculation as the ghosts),
  2. every descriptor the new run has is guessed from its knob settings, with range and confidence,
  3. after ingesting, each guess is compared with what the run really produced, and the result is
     stored as a (:Test)-[:TESTED]->(:Simulation) node: the track record is the set of these.

If the new run's knobs match an open ghost, that ghost is checked the same way against its own stored
guesses, marked verified and linked to the run: (:Prediction)-[:VERIFIED_BY]->(:Simulation).
Only real runs are ever tested; nothing here makes data up.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver

from milo_app import graph, predict
from milo_app.bundle import Bundle
from milo_app.config import DATA

# Every run this app has ever taken in, kept outside the database: a run already in it is never
# blind-tested again, even if the database is rebuilt from the drop folder.
LEDGER = DATA / "runs_seen.txt"


def seen() -> set[str]:
    try:
        return set(LEDGER.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def remember(bundle_ids: list[str]) -> None:
    new = [b for b in bundle_ids if b not in seen()]
    if new:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write("".join(b + "\n" for b in new))


def bundle_values(bundle: Bundle) -> dict[str, tuple[Any, str | None]]:
    """A bundle's descriptors, keyed exactly like graph.descriptor_table() keys them."""
    values: dict[str, tuple[Any, str | None]] = {}
    for item in bundle.items:
        value = graph._descriptor_value(json.dumps(item.value, default=str))
        if value is not None:
            values[f"{item.sector.lower()}:{item.name}"] = (value, item.units)
    manifest = bundle.manifest
    for key, raw in manifest.items():
        if key in graph.NOT_DESCRIPTORS or key.endswith("_units") and key[:-6] in manifest:
            continue
        value = graph._descriptor_value(json.dumps(raw, default=str))
        if value is not None:
            values[f"bundle:{key}"] = (value, manifest.get(f"{key}_units"))
    return values


def predict_new_run(driver: Driver, bundle: Bundle) -> dict[str, Any] | None:
    """Guess every descriptor of a run that is not in the graph yet, from the runs that are.
    Returns None for a run already in the graph (a re-ingest is not a blind test)."""
    table = graph.descriptor_table(driver)
    remember([s["id"] for s in table])  # everything already in the graph has been seen
    if bundle.bundle_id in seen():
        return None  # taken in before (a re-ingest or a rebuilt database): not a blind test
    values = bundle_values(bundle)
    base = {"bundle_id": bundle.bundle_id, "made_at": _now(), "runs_before": len(table)}
    if not table:
        return base | {"testable": False, "reason": "the first run: nothing to predict it from"}
    result = predict.calculate(table, 0)
    own = [k for k in result["knobs"] if isinstance(values.get(k, (None,))[0], float)]
    label = ", ".join(predict._knob_name(k) for k in own) or "no knobs"
    models = [m for m in result["models"] if m.get("family") == label]
    if not models:
        return base | {"testable": False, "reason": "the first run of its kind: no earlier run has the same knobs"}
    knob_values = {k: values[k][0] for k in own}
    outside = [k for k in own if not _tried_range(result, table, k)[0] <= knob_values[k] <= _tried_range(result, table, k)[1]]
    # A requested setting that was the same in every earlier run of this kind, but not in this one: the
    # model has never seen it vary, so it cannot know its effect. Flagged, so the result reads fairly.
    family_rows = [s for s in table if all(k in s["values"] for k in own)]
    never_varied = []
    for key, (value, _units) in values.items():
        if not key.startswith("input:requested.") or key in own:
            continue
        before = {s["values"][key][0] for s in family_rows if key in s["values"]}
        if len(before) == 1 and value not in before:
            never_varied.append({"key": key, "now": value, "before": next(iter(before))})
    guesses = {}
    for m in models:
        key = next((a for a in (m.get("aliases") or [m["descriptor"]]) if a in values), None)
        if key is not None:
            guesses[key] = predict.guess(m, knob_values) | {"units": m.get("units"), "method": m.get("method")}
    return base | {"testable": True, "calc_id": result["calc_id"], "family": label,
                   "knobs": {k: knob_values[k] for k in own}, "outside_tried": outside,
                   "never_varied": never_varied, "guesses": guesses}


def check(guesses: dict[str, dict], values: dict[str, tuple[Any, str | None]]) -> dict[str, Any]:
    """Compare guesses with the real values: one line per descriptor, and a summary."""
    lines = []
    for key, g in guesses.items():
        if key not in values:
            continue
        real = values[key][0]
        guessed = g.get("guess")
        line = {"key": key, "guess": guessed, "real": real, "confidence": g.get("confidence"), "units": g.get("units")}
        if isinstance(real, float) and isinstance(guessed, (int, float)) and "low" in g:
            line |= {"low": g["low"], "high": g["high"], "error": real - guessed,
                     "inside": bool(g["low"] <= real <= g["high"]),
                     "right": bool(abs(real - guessed) <= g["tolerance"])}
        elif isinstance(real, float) and isinstance(guessed, (int, float)):
            line["right"] = math.isclose(real, float(guessed), rel_tol=1e-9, abs_tol=1e-12)
        else:
            line["right"] = str(real) == str(guessed)
        lines.append(line)
    ranged = [ln for ln in lines if "inside" in ln]
    stated = [ln["confidence"] for ln in lines if isinstance(ln.get("confidence"), (int, float))]
    return {
        "lines": lines,
        "values": len(lines),
        "right": sum(ln["right"] for ln in lines),
        "stated": sum(stated) / len(stated) if stated else None,
        "ranged": len(ranged),
        "inside": sum(ln["inside"] for ln in ranged),
    }


def record(driver: Driver, bundle: Bundle, prediction: dict[str, Any] | None) -> dict[str, Any] | None:
    """After ingest: store the blind test, and check any ghost this run turns out to be."""
    remember([bundle.bundle_id])
    if prediction is None:
        return None
    values = bundle_values(bundle)
    test = dict(prediction)
    if prediction.get("testable"):
        test |= check(prediction.pop("guesses"), values)
        test.pop("guesses", None)
    graph.put_test(driver, test | {"id": "test-" + bundle.bundle_id})
    _verify_ghosts(driver, bundle, values)
    return test


def _verify_ghosts(driver: Driver, bundle: Bundle, values: dict[str, tuple[Any, str | None]]) -> None:
    for ghost in graph.list_predictions(driver):
        if ghost.get("source") not in ("calculation", "mcp"):
            continue
        proposed = json.loads(ghost.get("inputs_json") or "{}")
        if not proposed or not all(_same(values.get(_as_key(k), (None,))[0], v.get("value") if isinstance(v, dict) else v)
                                   for k, v in proposed.items()):
            continue
        guesses = {_as_key(k): (g if isinstance(g, dict) else {"guess": g}) | (
            {"guess": g["value"]} if isinstance(g, dict) and "guess" not in g and "value" in g else {})
            for k, g in json.loads(ghost.get("predicted_json") or "{}").items()}
        result = check(guesses, values)
        graph.verify_prediction(driver, ghost["id"], bundle.bundle_id, result | {"verified_at": _now()})


def _as_key(name: str) -> str:
    """Ghosts from the app are keyed like "output:X"; ghosts from the MCP may use the bare name."""
    return name if ":" in name else f"output:{name}" if not name.startswith("requested.") else f"input:{name}"


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, (int, float)):
        return math.isclose(a, float(b), rel_tol=1e-6, abs_tol=1e-9)
    return a is not None and str(a) == str(b)


def _tried_range(result: dict[str, Any], table: list[dict], knob: str) -> tuple[float, float]:
    tried = [s["values"][knob][0] for s in table if knob in s["values"] and isinstance(s["values"][knob][0], float)]
    return (min(tried), max(tried)) if tried else (-math.inf, math.inf)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
