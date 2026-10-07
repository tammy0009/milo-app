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
from milo_app import predict

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


def _in_campaign(campaign_name: str | None, wanted: str | None) -> bool:
    """Is a run whose campaign is `campaign_name` in the campaign asked for? "none" asks for runs in no campaign."""
    return wanted is None or (campaign_name or "none").lower() == wanted.strip().lower()


def graph_data(fields: list[str] | None = None, campaign: str | None = None) -> dict[str, Any]:
    """Every simulation with its bundle fields, inputs and outputs, plus the predictions already made.
    `campaign` keeps only that campaign's runs (and the predictions based on them)."""
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
            "p.inputs_json AS inputs, p.predicted_json AS predicted, p.reasoning AS reasoning, "
            "COLLECT { MATCH (p)-[:BASED_ON]->(s) RETURN s.bundle_id } AS based_on"
        )
    rows = [r for r in rows if _in_campaign(app_graph.campaign_of(r["s"]), campaign)]
    if campaign is not None:
        in_scope = {r["s"]["bundle_id"] for r in rows}
        preds = [p for p in preds if set(p["based_on"]) & in_scope]
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
    predictions = [_prediction(p, keep, wanted is not None) for p in preds]
    return {"simulations": sims, "predictions": predictions}


GUESS_FIELDS = ("guess", "low", "high", "confidence", "units")


def _prediction(p: dict[str, Any], keep, filtered: bool) -> dict[str, Any]:
    """A prediction as the LLM reads it. A calculated ghost guesses every descriptor (hundreds of values), so
    its guesses are shown only for the descriptors `fields` asked for, and slimmed to the guess, its range,
    its confidence and its units. The MCP's own predictions are small and shown whole."""
    predicted = json.loads(p["predicted"] or "{}")
    item = {"id": p["id"], "title": p["title"], "made_by": p["source"], "confidence": p["confidence"],
            "proposed_inputs": json.loads(p["inputs"] or "{}")}
    if p["source"] == "mcp":
        item["predicted"] = predicted
    elif filtered:
        item["predicted"] = {k: {f: v[f] for f in GUESS_FIELDS if isinstance(v, dict) and f in v}
                             for k, v in predicted.items() if keep(k)}
        item["values_guessed_in_all"] = len(predicted)
    else:
        item["values_guessed_in_all"] = len(predicted)
        item["predicted"] = "left out: this ghost guesses %d values. Pass fields=[...] to see the ones you need." % len(predicted)
    if p["reasoning"]:
        item["reasoning"] = p["reasoning"]
    return item


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


# ---------------------------------------------------------------------------- the app's math, for the MCP


def _campaign_table(campaign: str | None) -> list[dict[str, Any]]:
    """The app's descriptor table (every run's numbers), over one campaign's runs when asked."""
    with _driver() as driver:
        table = app_graph.descriptor_table(driver)
    return [row for row in table
            if _in_campaign(app_graph.campaign_of({"campaign": row["values"].get("bundle:campaign", (None,))[0]}), campaign)]


def _plain_name(key: str) -> str:
    return key.partition(":")[2]


def _resolve(name: str, catalog: list[dict[str, Any]]) -> str:
    """A descriptor key from what the caller typed: the full key ("output:E_int"), the name, or a part of
    the name (case-insensitive). Ambiguous or unknown names are refused with the choices."""
    text = name.strip().lower()
    keys = [e["key"] for e in catalog]
    for match in (lambda k: k.lower() == text, lambda k: _plain_name(k).lower() == text,
                  lambda k: text in _plain_name(k).lower()):
        found = [k for k in keys if match(k)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ValueError(f"'{name}' matches more than one descriptor: {', '.join(found[:12])}. Use the full key.")
    raise ValueError(f"no numeric descriptor '{name}' that changes between these runs. Choices: "
                     + ", ".join(keys[:40]))


def _py(value: Any) -> Any:
    """numpy numbers (and containers of them) as plain Python, so the result serialises."""
    if isinstance(value, dict):
        return {k: _py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_py(v) for v in value]
    if hasattr(value, "item") and callable(value.item):
        return value.item()
    return value


def _decoded(value: Any) -> Any:
    """Lists the app stores as JSON text, back to lists."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def relationships(descriptor: str = "", campaign: str | None = None, min_confidence: float = 0.0,
                  limit: int = 25, include_points: bool = False) -> dict[str, Any]:
    """The app's relationships between numeric descriptors, strongest first. Over every run (the stored
    ones), or worked out again over one campaign's runs, exactly as the Ghosts tab does."""
    if campaign is None:
        with _driver() as driver:
            found = app_graph.list_relationships(driver)
        scope = "every run"
    else:
        table = _campaign_table(campaign)
        if not table:
            return {"error": f"no runs in campaign '{campaign}'"}
        found = predict.relationships_in(table)
        scope = f"the {len(table)} runs of campaign '{campaign}'"
    text = descriptor.strip().lower()
    out = []
    for rel in found:
        rel = {k: _decoded(v) for k, v in rel.items()}
        names = [str(rel.get("a", "")), str(rel.get("b", "")), *rel.get("a_keys", []), *rel.get("b_keys", [])]
        if text and not any(text in n.lower() for n in names):
            continue
        if float(rel.get("confidence") or 0.0) < min_confidence:
            continue
        item = {"name": rel.get("name"), "a": rel.get("a"), "b": rel.get("b"), "kind": rel.get("kind"),
                "runs": rel.get("n"), "r": rel.get("r"), "p": rel.get("p"), "confidence": rel.get("confidence"),
                "lfdr": rel.get("lfdr"), "a_units": rel.get("a_units"), "b_units": rel.get("b_units"),
                **({"note": rel["note"]} if rel.get("note") else {})}
        if include_points:
            item["points"] = rel.get("points")
        out.append({k: v for k, v in item.items() if v is not None})
    out.sort(key=lambda r: (-(r.get("confidence") or 0.0), -abs(r.get("r") or 0.0)))
    return _py({"scope": scope,
            "how_to_read": ("kind 'cause' = one side is a knob the user set; 'association' = two measured values that move "
                            "together (not proof either causes the other). confidence = 1 - local false discovery rate; "
                            "with few runs it is low on purpose. Values built from a knob (chain counts, the built mass "
                            "fraction, density of a layer made from it) rank high because they restate the knob, not "
                            "because they explain it: when a measured value scores about the same as the knob itself, "
                            "the runs cannot tell the two apart."),
            "total_matching": len(out), "relationships": out[:max(1, limit)]})


def formula(target: str, predictors: list[str], campaign: str | None = None,
            at: dict[str, float] | None = None) -> dict[str, Any]:
    """How `target` follows from `predictors` (the app's Bayesian linear regression, over every run that has
    them all, or one campaign's), and, when `at` gives predictor values, the guess there with its 90 % range."""
    table = _campaign_table(campaign)
    if not table:
        return {"error": f"no runs in campaign '{campaign}'" if campaign else "the graph has no runs"}
    try:
        catalog = predict.formula_catalog(table)
        target_key = _resolve(target, catalog)
        keys = [_resolve(p, catalog) for p in predictors]
        at_keys = {_resolve(name, catalog): float(v) for name, v in (at or {}).items()}
    except ValueError as error:
        return {"error": str(error)}
    fit = predict.formula(table, target_key, keys)
    if "error" in fit:
        return fit
    out: dict[str, Any] = {
        "scope": f"campaign '{campaign}'" if campaign else "every run",
        "target": target_key, "target_units": fit["units"], "runs": fit["n"], "sims": fit["sims"],
        "method": fit["method"], "prior": fit["prior"], "degrees_of_freedom": fit["df"],
        "predictors": fit["predictors"], "predictor_units": fit["predictor_units"],
        "left_out_because_constant": fit["left_out"],
        "intercept": fit["intercept"], "slopes": dict(zip(fit["predictors"], fit["slopes"])),
        "slope_sd": dict(zip(fit["predictors"], fit["slope_sd"])), "noise_sd": fit["noise_sd"], "r2": fit["r2"],
        "observed": fit["observed"], "fitted": fit["fitted"],
        "inputs": fit["inputs"],
    }
    if at_keys:
        missing = [k for k in fit["predictors"] if k not in at_keys]
        if missing:
            out["guess_error"] = "give a value for: " + ", ".join(missing)
        else:
            point = {k: at_keys[k] for k in fit["predictors"]}
            guessed = predict.guess(fit, point)
            outside = [k for k, lo, hi in zip(fit["predictors"], fit["x_lo"], fit["x_hi"]) if not lo <= point[k] <= hi]
            out["guess_at"] = point
            out["guess"] = {k: v for k, v in guessed.items() if k in ("guess", "low", "high", "confidence",
                                                                   "typical_error", "tolerance")}
            if outside:
                out["warning"] = ("outside the range these runs cover for " + ", ".join(outside)
                                  + ": this is extrapolation, and the range does not account for it")
    return _py(out)


# ---------------------------------------------------------------------------- the fixed evidence pass


FEW_RUNS = 8  # below this many runs, every finding is a lead, not a result
NEAR_KNOB = 0.05  # a value this close to the knob's own |r| cannot be told apart from it
RESTATES = 0.95  # |r| with a knob at least this high: the value is the knob in other words


def investigate(campaign: str, target: str, top: int = 8) -> dict[str, Any]:
    """The same steps, in the same order, for any question about one result in one campaign: the runs ranked on
    it, how each knob explains it, what else moves with it and whether that is a second window onto a knob,
    and what the runs cannot settle. Everything is computed here; nothing is left to the caller's arithmetic."""
    table = _campaign_table(campaign)
    if not table:
        return {"error": f"no runs in campaign '{campaign}'"}
    columns = predict._columns(table)
    knobs = predict._knobs(columns, len(table))
    groups = predict._merge_duplicates(columns, knobs)
    try:
        target_key = _resolve(target, predict.formula_catalog(table))
    except ValueError as error:
        return {"error": str(error)}
    rep = next(r for r, keys in groups.items() if target_key in keys)
    rows = columns[target_key]["rows"]
    name = predict.pretty_name(_plain_name(target_key))
    units = columns[target_key]["units"]

    # 1. the runs, ranked on the target
    used = [k for k in knobs if all(r in columns[k]["at"] for r in rows) and len({predict._value(columns, k, r) for r in rows}) > 1]
    ranked = sorted(rows, key=lambda r: predict._value(columns, target_key, r))
    runs = [{"bundle_id": table[r]["id"], "title": table[r].get("title"), "value": predict._value(columns, target_key, r),
             **{predict._knob_name(k): predict._value(columns, k, r) for k in used}} for r in ranked]

    # 2. each knob against the target
    fits = []
    for k in used:
        fit = predict.formula(table, target_key, [k])
        if "error" in fit:
            continue
        fits.append({"knob": predict._knob_name(k), "key": k, "runs": fit["n"], "slope": fit["slopes"][0] if fit["slopes"] else None,
                     "slope_sd": fit["slope_sd"][0] if fit["slope_sd"] else None, "r2": fit["r2"],
                     "noise_sd": fit["noise_sd"], "degrees_of_freedom": fit["df"], "units_per_knob_unit": f"{units or ''} per {columns[k]['units'] or 'unit'}"})
    fits.sort(key=lambda f: -(f["r2"] or 0.0))

    # 3. what else moves with the target, and 4. whether each is a second window onto a knob
    rels = predict.relationships_in(table)
    between = {frozenset((r["a"], r["b"])): r for r in rels}
    knob_r = [abs(r["r"]) for r in rels if "r" in r and ((r["a"] == rep and r["b"] in knobs) or (r["b"] == rep and r["a"] in knobs))]
    best_knob_r = max(knob_r, default=None)
    related, pairs_tested = [], None
    for rel in rels:
        if rep not in (rel["a"], rel["b"]) or "r" not in rel:
            continue
        other = rel["b"] if rel["a"] == rep else rel["a"]
        if other in knobs:
            continue
        pairs_tested = rel.get("pairs_tested", pairs_tested)
        hop = []
        for k in used:
            link = between.get(frozenset((other, k)))
            if link and "r" in link:
                hop.append({"knob": predict._knob_name(k), "r": link["r"], "confidence": link.get("confidence"), "runs": link["n"]})
        related.append({
            "name": predict.pretty_name(_plain_name(other)), "key": other, "units": columns[other]["units"],
            "runs": rel["n"], "r": rel["r"], "confidence": rel.get("confidence"),
            "ties_the_knob": best_knob_r is not None and abs(rel["r"]) >= best_knob_r - NEAR_KNOB,
            "restates_a_knob": any(abs(h["r"]) >= RESTATES for h in hop),
            "to_the_knobs": hop,
        })
    related.sort(key=lambda x: (-(x["confidence"] or 0.0), -abs(x["r"])))
    related = related[:max(1, top)]

    # 5. what the runs cannot settle
    flags = []
    if len(rows) < FEW_RUNS:
        flags.append(f"only {len(rows)} runs have this result: treat every finding as a lead, and every range as wide")
    if len(rows) < len(table):
        flags.append(f"{len(table) - len(rows)} of the campaign's {len(table)} runs have no {name}: they are left out")
    if not used:
        flags.append("no knob changes between the runs that have this result: nothing here can say what drives it")
    restating = [x["name"] for x in related if x["restates_a_knob"]]
    if restating:
        flags.append(f"{', '.join(restating)}: each is the knob seen another way (|r| >= {RESTATES} with it), "
                     "so none is a separate explanation and these runs cannot tell them apart")
    tying = [x["name"] for x in related if x["ties_the_knob"] and not x["restates_a_knob"]]
    if tying:
        flags.append(f"{', '.join(tying)}: moves with {name} about as strongly as the knob does, so these runs cannot tell them apart")
    if len(used) == 1:
        along = [predict._value(columns, target_key, r) for r in sorted(rows, key=lambda r: predict._value(columns, used[0], r))]
        steps = [b - a for a, b in zip(along, along[1:]) if b != a]
        if steps and not (all(s > 0 for s in steps) or all(s < 0 for s in steps)):
            flags.append(f"{name} is not monotonic in {predict._knob_name(used[0])} (it goes up and down along the knob): "
                         "a straight line misses the shape, and the fit above is only a rough summary")
    if fits and (fits[0]["r2"] or 0) < 0.5:
        flags.append(f"the best knob explains only {fits[0]['r2']:.0%} of the variation in {name}: something else matters")
    return _py({
        "scope": f"campaign '{campaign}'", "target": name, "target_key": target_key, "units": units,
        "runs_with_target": len(rows), "runs_in_campaign": len(table),
        "knobs_varied": [predict._knob_name(k) for k in used],
        "runs_ranked_lowest_to_highest": runs,
        "each_knob_against_target": fits,
        "moves_with_target": related, "pairs_tested_for_confidence": pairs_tested,
        "flags": flags,
        "how_to_read": ("Runs are ranked lowest to highest; which end is 'better' depends on the quantity (for an "
                        "interaction energy, more negative is stronger binding). Slopes are per unit of the knob. "
                        "'confidence' is 1 - local false discovery rate. Everything here is measured or fitted by "
                        "code: quote it, label your own ideas as hypotheses, and add no numbers of your own."),
    })
