"""The MCP's window into the MILO app's graph: read the simulations, and add predictions
(ghost nodes) reasoned out by the MCP itself. Uses the app's own graph connection and settings.

A reasoned prediction is stored exactly like the app's calculated ones, marked source "mcp" and
carrying its reasoning, so the app shows it as a ghost node with "Made by: the MCP". The app's
own recalculation never touches it.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from milo_app import graph as app_graph

# Simulation properties the app sets itself (not part of the bundle).
APP_FIELDS = {"source_path", "fingerprint", "ingested_at", "input_count", "output_count", "file_count",
              "manifest_bundle_id", "failed", "error", "campaign_folder"}


def _driver():
    return app_graph.connect()


def _value(value_json: str) -> Any:
    try:
        value = json.loads(value_json)
    except (TypeError, ValueError):
        return value_json
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "…"
    if isinstance(value, (list, dict)) and len(json.dumps(value)) > 200:
        return f"<{type(value).__name__} of {len(value)} items>"
    return value


def graph_data(fields: list[str] | None = None) -> dict[str, Any]:
    """Every simulation with its bundle fields, inputs and outputs, plus the predictions already made."""
    wanted = {f.lower() for f in fields} if fields else None

    def keep(name: str) -> bool:
        return wanted is None or any(w in name.lower() for w in wanted)

    with _driver() as driver:
        rows, _, _ = driver.execute_query(
            """
            MATCH (s:Simulation)
            RETURN properties(s) AS s,
                   COLLECT { MATCH (s)-[:HAS_INPUT]->(n) RETURN [n.name, n.value_json, n.units] } AS inputs,
                   COLLECT { MATCH (s)-[:HAS_OUTPUT]->(n) RETURN [n.name, n.value_json, n.units] } AS outputs
            ORDER BY s.start
            """
        )
        preds, _, _ = driver.execute_query(
            "MATCH (p:Prediction) RETURN p.id AS id, p.title AS title, p.source AS source, p.confidence AS confidence, "
            "p.inputs_json AS inputs, p.predicted_json AS predicted, p.reasoning AS reasoning"
        )
    sims = []
    for r in rows:
        s = r["s"]
        sims.append({
            "bundle_id": s["bundle_id"],
            "bundle": {k: v for k, v in s.items() if k not in APP_FIELDS and k != "bundle_id"},
            "failed": bool(s.get("failed")),
            **({"error": s.get("error")} if s.get("error") else {}),
            "inputs": {n: {"value": _value(v), "units": u} for n, v, u in r["inputs"] if keep(n)},
            "outputs": {n: {"value": _value(v), "units": u} for n, v, u in r["outputs"] if keep(n)},
        })
    predictions = [{"id": p["id"], "title": p["title"], "made_by": p["source"], "confidence": p["confidence"],
                    "proposed_inputs": json.loads(p["inputs"] or "{}"), "predicted": json.loads(p["predicted"] or "{}"),
                    **({"reasoning": p["reasoning"]} if p["reasoning"] else {})} for p in preds]
    return {"simulations": sims, "predictions": predictions}


def campaigns() -> dict[str, Any]:
    """Every campaign in the graph, newest first, and how many runs are not in one."""
    with _driver() as driver:
        rows, _, _ = driver.execute_query(
            """
            MATCH (s:Simulation)
            RETURN s.bundle_id AS id, s.title AS title, s.campaign AS campaign, s.failed AS failed,
                   s.campaign_folder AS folder, coalesce(s.finish, s.start, s.ingested_at) AS landed,
                   COLLECT { MATCH (s)-[:HAS_INPUT]->(n) WHERE n.name STARTS WITH 'requested.'
                             RETURN [n.name, n.value_json] } AS knobs
            """
        )
    found: dict[str, dict[str, Any]] = {}
    outside = 0
    for r in rows:
        name = app_graph.campaign_of({"campaign": r["campaign"]})
        if name is None:
            outside += 1
            continue
        c = found.setdefault(name, {"campaign": name, "folder": app_graph.campaign_folder(name), "runs": [],
                                    "failed": 0, "first": r["landed"], "last": r["landed"], "_knobs": {}})
        c["runs"].append({"bundle_id": r["id"], "title": r["title"], "landed": r["landed"], "failed": bool(r["failed"])})
        c["failed"] += bool(r["failed"])
        c["first"], c["last"] = min(c["first"], r["landed"]), max(c["last"], r["landed"])
        for knob, value in r["knobs"]:
            c["_knobs"].setdefault(knob, set()).add(value)
    listed = []
    for c in sorted(found.values(), key=lambda c: c["last"], reverse=True):
        c["knobs_varied"] = sorted(k for k, values in c.pop("_knobs").items() if len(values) > 1)
        c["runs"].sort(key=lambda run: run["landed"], reverse=True)
        listed.append(c)
    return {"campaigns": listed, "runs_not_in_a_campaign": outside}


def _error_kind(error: str | None) -> str:
    """An error with its numbers blanked, so the same failure in different runs groups together."""
    return re.sub(r"[-+]?\d[\d.,eE+-]*", "#", error or "(no error recorded)")[:160]


def run_errors(campaign: str | None = None, include_traceback: bool = True) -> dict[str, Any]:
    """Every failed run, and for each campaign how many failed and whether they failed the same way."""
    with _driver() as driver:
        issues = app_graph.run_issues(driver)
        totals, _, _ = driver.execute_query("MATCH (s:Simulation) RETURN s.campaign AS campaign, count(*) AS runs")
    runs_in = {app_graph.campaign_of({"campaign": r["campaign"]}): r["runs"] for r in totals}
    if campaign:
        wanted = campaign.strip().lower()
        issues = [i for i in issues if (i["campaign"] or "none").lower() == wanted]
    for issue in issues:
        if not include_traceback:
            issue.pop("traceback", None)
        elif issue.get("traceback"):
            issue["traceback"] = "\n".join(str(issue["traceback"]).splitlines()[-25:])
        issue["still_open"] = not any(not r["failed"] for r in issue["redone_by"])
    campaigns = []
    for name in sorted({i["campaign"] for i in issues}, key=lambda c: (c is None, c or "")):
        failed = [i for i in issues if i["campaign"] == name]
        open_ = [i for i in failed if i["still_open"]]
        kinds: dict[str, list[str]] = {}
        for i in failed:
            kinds.setdefault(_error_kind(i["error"]), []).append(i["title"] or i["bundle_id"])
        total = runs_in.get(name, len(failed))
        shared = max(kinds.values(), key=len) if kinds else []
        if len(shared) >= 2 and len(shared) >= total / 2:
            verdict = (f"{len(shared)} of {total} runs failed with the same error: most likely the script or the "
                       "setup, not the settings. Fix the cause, then consider running the whole campaign again.")
        elif len(failed) == total and total > 1:
            verdict = f"every run failed ({total}), in different ways: look at the script and the setup first."
        else:
            verdict = (f"{len(failed)} of {total} runs failed, in different ways: redo them one by one "
                       "(milo_generate_script with redo_of), after fixing what each error points to.")
        campaigns.append({"campaign": name, "runs": total, "failed": len(failed), "still_open": len(open_),
                          "errors": [{"error": k, "runs": v} for k, v in sorted(kinds.items(), key=lambda kv: -len(kv[1]))],
                          "verdict": verdict})
    return {"failed_runs": issues, "campaigns": campaigns,
            "summary": (f"{sum(i['still_open'] for i in issues)} failed run(s) not redone yet, "
                        f"{len(issues)} failed in all") if issues else "no failed runs"}


def _named_values(values: dict[str, Any], what: str) -> dict[str, dict[str, Any]]:
    """{name: value} or {name: {"value", "units", "spread"}} -> the second form."""
    out = {}
    for name, v in values.items():
        v = dict(v) if isinstance(v, dict) else {"value": v}
        if "value" not in v:
            raise ValueError(f"{what} '{name}' has no value")
        out[str(name)] = {k: v[k] for k in ("value", "units", "spread") if k in v}
    return out


def add_prediction(title: str, proposed_inputs: dict[str, Any], predicted: dict[str, Any], confidence: float,
                   reasoning: str, based_on: list[str], model: str = "MCP reasoning") -> dict[str, Any]:
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if not reasoning.strip():
        raise ValueError("reasoning is required: it is what the confidence rests on")
    row = {
        "id": "mcp-" + str(uuid.uuid4()),
        "title": title,
        "source": "mcp",
        "model": model,
        "confidence": float(confidence),
        "reasoning": reasoning,
        "inputs_json": json.dumps(_named_values(proposed_inputs, "proposed input")),
        "predicted_json": json.dumps(_named_values(predicted, "predicted output")),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _driver() as driver:
        found, _, _ = driver.execute_query(
            "UNWIND $ids AS id MATCH (s:Simulation {bundle_id: id}) RETURN collect(s.bundle_id) AS found", ids=based_on
        )
        missing = sorted(set(based_on) - set(found[0]["found"]))
        if missing:
            raise ValueError(f"no simulation with bundle_id {', '.join(missing)}")
        driver.execute_query(
            """
            CREATE (p:Prediction) SET p = $row
            WITH p UNWIND $based_on AS id MATCH (s:Simulation {bundle_id: id}) MERGE (p)-[:BASED_ON]->(s)
            """,
            row=row, based_on=based_on,
        )
    return {"id": row["id"], "stored": True, "note": "The MILO app shows it as a ghost node on its next refresh."}


def remove_prediction(prediction_id: str) -> dict[str, Any]:
    """Only the MCP's own predictions can be removed this way; calculated ones follow the data."""
    with _driver() as driver:
        rows, _, _ = driver.execute_query(
            "MATCH (p:Prediction {id: $id, source: 'mcp'}) DETACH DELETE p RETURN count(*) AS n", id=prediction_id
        )
    return {"removed": bool(rows and rows[0]["n"])}
