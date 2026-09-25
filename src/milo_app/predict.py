"""The ghost calculator: the first method of ghost.md, recalculated from every simulation each time
the graph changes. ghost.md is the rule book; the section numbers below point into it.

One recalculation makes three things, all stored in the graph and in a calculation record:

  models         how each descriptor is guessed from the knobs                    (ghost.md 4.1, 4.2)
  relationships  every pair of numeric descriptors, with the confidence it is real  (ghost.md 5.3)
  ghosts         a real run with one knob moved, and a guess for every descriptor  (ghost.md 4.3, 5.1, 5.2)

It also answers formula questions on demand (formula()): how one descriptor follows from others,
for the app's Ghosts tab.

Everything is deterministic: the same simulations give the same numbers, bit for bit.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from neo4j import Driver
from scipy import stats
from statsmodels.stats.multitest import NullDistribution, local_fdr

from milo_app import graph
from milo_app.config import DATA
from milo_app.ui.format import pretty_name, short_title

log = logging.getLogger(__name__)

SOURCE = "calculation"
METHOD = "MILO ghost method 1 (ghost.md)"
METHOD_VERSION = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
RECORDS = DATA / "ghosts"

# ghost.md 4.1: priors of the Bayesian linear regression, on the standardised scale.
PRIOR_SLOPE_VAR = 1.0  # each slope ~ Normal(0, sigma^2 * 1)
PRIOR_INTERCEPT_VAR = 100.0  # the intercept is left nearly free
PRIOR_A, PRIOR_B = 1.0, 1.0  # sigma^2 ~ Inverse-Gamma(1, 1)
# ghost.md 5.1 / 13
TOLERANCE = 0.05  # "right" = within 5 % of the guess, at least 5 % of the range seen
RANGE = 0.90  # ranges are drawn at 90 %
# ghost.md 5.3: fewer testable pairs than this and the empirical null cannot be estimated; the
# textbook null with every pair assumed unrelated (the cautious default) is used instead.
MIN_PAIRS_FOR_EMPIRICAL_NULL = 100
# ghost.md 4.3: a ghost's new knob value must differ from its run's by at least this share of the
# knob's tried range, and is rounded to a tenth of the range's order of magnitude (0.4 -> 0.01).
MIN_MOVE = 0.05


# ---------------------------------------------------------------------------- entry point


def refresh(driver: Driver, count: int) -> dict[str, Any]:
    """Recalculate and store everything. Returns a short summary."""
    try:
        table = graph.descriptor_table(driver)
        result = calculate(table, count)
    except Exception:  # noqa: BLE001 - a failed calculation must never stop the app
        log.exception("ghost calculation failed")
        return {"error": True}
    graph.replace_nodes(driver, "Model", SOURCE, result["models"])
    graph.replace_nodes(driver, "Relationship", SOURCE, result["relationships"])
    graph.replace_predictions(driver, SOURCE, result["ghosts"])
    _write_record(table, result)
    return {"calc_id": result["calc_id"], "models": len(result["models"]),
            "relationships": len(result["relationships"]), "ghosts": len(result["ghosts"])}


def calculate(table: list[dict[str, Any]], count: int) -> dict[str, Any]:
    fingerprints = sorted(f"{s['id']}:{s.get('fingerprint')}" for s in table)
    calc_id = hashlib.sha1(("|".join(fingerprints) + METHOD_VERSION).encode()).hexdigest()[:16]
    sims = [s["id"] for s in table]
    columns = _columns(table)
    knobs = _knobs(columns, len(table))
    groups = _merge_duplicates(columns, knobs)  # representative key -> every key it stands for
    stamp = {"calc_id": calc_id, "calc_method": METHOD, "method_version": METHOD_VERSION}

    # Families (ghost.md 4.1): runs that have the same knobs are one kind of experiment, and each
    # descriptor is modelled within a family, so a different kind of simulation never blurs another's.
    families: dict[tuple, list[int]] = {}
    for i in range(len(table)):
        families.setdefault(tuple(k for k in knobs if i in columns[k]["at"]), []).append(i)
    models = {}
    for family, members in families.items():
        member = set(members)
        label = ", ".join(_knob_name(k) for k in family) or "no knobs"
        for rep, keys in groups.items():
            if rep in knobs:
                continue  # a knob is set, not guessed
            rows = [r for r in columns[rep]["rows"] if r in member]
            if not rows:
                continue
            model_id = "model:" + rep + ("" if len(families) == 1 else "@" + hashlib.sha1(label.encode()).hexdigest()[:6])
            models[model_id] = _model(rep, keys, columns, list(family), sims, rows) | stamp | {
                "id": model_id, "family": label}
    relationships = [r | stamp for r in _relationships(groups, columns, knobs, sims)]
    titles = {s["id"]: s.get("title") or s["id"] for s in table}
    ghosts = [g | stamp for g in _ghosts(models, columns, knobs, sims, count, titles)]
    return {"calc_id": calc_id, "knobs": knobs, "groups": groups, "models": list(models.values()),
            "relationships": relationships, "ghosts": ghosts}


# ---------------------------------------------------------------------------- data


def _columns(table: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """key -> {"rows": sim indexes that have it, "values": [...], "units", "numeric"}."""
    columns: dict[str, dict[str, Any]] = {}
    for i, sim in enumerate(table):
        for key, (value, units) in sim["values"].items():
            col = columns.setdefault(key, {"rows": [], "values": [], "units": units})
            col["rows"].append(i)
            col["values"].append(value)
    for col in columns.values():
        col["at"] = {row: position for position, row in enumerate(col["rows"])}
        col["numeric"] = all(isinstance(v, float) for v in col["values"])
        if col["numeric"]:
            col["values"] = np.array(col["values"])
    return columns


def _knobs(columns: dict[str, dict[str, Any]], n: int) -> list[str]:
    """ghost.md 2: numeric requested.* inputs that differ between the runs that have them (a
    different kind of simulation without them does not stop them being knobs for the rest); knobs
    that always move together count once. Without any requested.* inputs, every numeric input that
    every run has and that differs between runs."""
    def varying(key: str) -> bool:
        c = columns[key]
        return c["numeric"] and len(c["rows"]) >= 2 and np.ptp(c["values"]) > 0

    requested = sorted(k for k in columns if k.startswith("input:requested.") and varying(k))
    candidates = requested or sorted(k for k in columns
                                     if k.startswith("input:") and len(columns[k]["rows"]) == n and varying(k))
    kept, shapes = [], set()
    for key in candidates:
        c = columns[key]
        v = c["values"]
        shape = (tuple(c["rows"]), tuple(np.round((v - v.min()) / np.ptp(v), 9)))
        mirror = (shape[0], tuple(np.round(1 - np.array(shape[1]), 9)))
        if shape not in shapes and mirror not in shapes:
            shapes.add(shape)
            kept.append(key)
    return kept


def _merge_duplicates(columns: dict[str, dict[str, Any]], knobs: list[str]) -> dict[str, list[str]]:
    """ghost.md 5.3: descriptors that are the same thing recorded twice (the very same values in the
    very same runs: Length A/B/C, one temperature in three places) become one; a knob is always the
    one that stands for the group. Only identical values merge: with few runs, different quantities
    easily share an up-and-down pattern by coincidence, and those must stay apart."""
    groups: dict[tuple, list[str]] = {}
    order = knobs + sorted(k for k in columns if k not in knobs)
    for key in order:
        col = columns[key]
        if col["numeric"] and np.ptp(col["values"]) > 0:
            signature = ("n", tuple(col["rows"]), tuple(float(f"{v:.9g}") for v in col["values"]))
        else:
            signature = ("k", key)  # constants and text stand alone
        groups.setdefault(signature, []).append(key)
    return {keys[0]: keys for keys in groups.values()}


def _value(columns: dict[str, dict[str, Any]], key: str, row: int) -> Any:
    """Run `row`'s value of `key`, or None if that run does not have it."""
    col = columns[key]
    position = col["at"].get(row)
    return None if position is None else col["values"][position]


def _design(raw: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """ghost.md 4.1: knobs scaled to 0-1 and centred, plus a column of ones for the intercept."""
    scaled = (raw - lo) / (hi - lo) - 0.5 if raw.size else raw.reshape(len(raw), 0)
    return np.column_stack([np.ones(len(raw)), scaled])


# ---------------------------------------------------------------------------- models (ghost.md 4.1, 4.2)


def _model(rep: str, keys: list[str], columns, knobs, sims, rows: list[int]) -> dict[str, Any]:
    """How `rep` is guessed, fitted to `rows` (one family's runs that have it)."""
    col = columns[rep]
    n = len(rows)
    base = {"id": "model:" + rep, "descriptor": rep, "aliases": keys, "name": pretty_name(rep.partition(":")[2]),
            "units": col["units"], "n": n, "sims": [sims[i] for i in rows]}
    values = [_value(columns, rep, r) for r in rows]
    if col["numeric"] and np.ptp(values) > 0:
        return base | _fit_regression(np.array(values, dtype=float), rows, columns, knobs)
    counts = Counter(values)
    guess, seen = counts.most_common(1)[0]
    k = len(counts) + 1  # the values seen, plus "something new"
    return base | {
        "method": "rule of succession",
        "guess": guess,
        "confidence": (seen + 1) / (n + k),
        "counts": {str(v): c for v, c in counts.items()},
        "categories": k,
    }


def _fit_regression(y: np.ndarray, rows: list[int], columns, knobs) -> dict[str, Any]:
    """Fitted on the knobs that every one of this descriptor's runs has."""
    usable = [k for k in knobs if all(r in columns[k]["at"] for r in rows)]
    x = (np.array([[_value(columns, k, r) for k in usable] for r in rows], dtype=float)
         if usable else np.zeros((len(rows), 0)))
    return _regress(y, x, usable) | {"knobs": usable}


def _regress(y: np.ndarray, x_raw: np.ndarray, names: list[str]) -> dict[str, Any]:
    """Bayesian linear regression, conjugate Normal-Inverse-Gamma prior, on the standardised scale
    (ghost.md 4.1): y = intercept + sum(slope_k * x_k). A predictor that does not change across
    these runs carries no information about y and is left out (listed under "left_out")."""
    keep = [j for j in range(x_raw.shape[1]) if np.ptp(x_raw[:, j]) > 0]
    predictors = [names[j] for j in keep]
    x_raw = x_raw[:, keep]
    lo, hi = (x_raw.min(axis=0), x_raw.max(axis=0)) if keep else (np.zeros(0), np.zeros(0))
    x = _design(x_raw, lo, hi) if keep else np.ones((len(y), 1))  # no predictors: the mean alone
    mu, sd = float(y.mean()), float(y.std(ddof=1)) if len(y) > 1 else 1.0
    z = (y - mu) / sd
    n, p = x.shape
    if n > p and np.linalg.matrix_rank(x) == p:
        # Enough runs to estimate the scatter from the data: the standard noninformative prior,
        # p(slopes, σ²) ∝ 1/σ² (Gelman et al., Bayesian Data Analysis, 14.2). Its 90 % ranges hold
        # 90 % of the time and it does not pull slopes toward zero.
        prior = "noninformative"
        vn = np.linalg.inv(x.T @ x)
        mn = vn @ x.T @ z
        residual = z - x @ mn
        df_resid = n - p
        s2 = max(float(residual @ residual) / df_resid, 1e-12)
        an, bn = df_resid / 2, s2 * df_resid / 2  # so that b_n / a_n = s² and 2·a_n = n − p
    else:
        # Too few runs for that (no scatter left to measure): the weakly informative conjugate
        # prior of ghost.md 4.1, which keeps ranges wide and confidence low, as they should be.
        prior = "conjugate"
        v0_inv = np.diag([1 / PRIOR_INTERCEPT_VAR] + [1 / PRIOR_SLOPE_VAR] * len(keep))
        vn = np.linalg.inv(v0_inv + x.T @ x)
        mn = vn @ x.T @ z
        an = PRIOR_A + len(y) / 2
        bn = PRIOR_B + 0.5 * float(z @ z - mn @ (v0_inv + x.T @ x) @ mn)
    # the same line in real units
    span = hi - lo
    slopes = sd * mn[1:] / span if keep else np.zeros(0)
    intercept = mu + sd * (mn[0] - float(np.sum(mn[1:] * (lo / span + 0.5)))) if keep else mu + sd * mn[0]
    slope_sd = sd * np.sqrt(bn / an * np.diag(vn)[1:]) / span if keep else np.zeros(0)
    fitted = mu + sd * (x @ mn)
    ss_total = float(np.sum((y - mu) ** 2))
    return {
        "method": "Bayesian linear regression", "prior": prior,
        "predictors": predictors, "left_out": [k for k in names if k not in predictors],
        "mean": mu, "spread": sd,
        "posterior_mean": mn.tolist(), "posterior_cov": vn.tolist(), "a_n": an, "b_n": bn, "df": 2 * an,
        "intercept": intercept, "slopes": slopes.tolist(), "slope_sd": slope_sd.tolist(),
        "noise_sd": sd * math.sqrt(bn / an) if an > 0 else None,
        "fitted": fitted.tolist(), "observed": y.tolist(),
        "r2": 1 - float(np.sum((y - fitted) ** 2)) / ss_total if ss_total > 0 else None,
        "range_seen": float(np.ptp(y)),
        "x_lo": lo.tolist(), "x_hi": hi.tolist(),
    }


def guess(model: dict[str, Any], values: dict[str, float]) -> dict[str, Any]:
    """One guessed value, given the values of the model's predictors (ghost.md 5.1)."""
    if model["method"] != "Bayesian linear regression":
        return {"guess": model["guess"], "confidence": model["confidence"], "method": model["method"]}
    point = np.array([values[p] for p in model["predictors"]], dtype=float)
    x = _design(point.reshape(1, -1), np.array(model["x_lo"]), np.array(model["x_hi"]))[0]
    mn, vn = np.array(model["posterior_mean"]), np.array(model["posterior_cov"])
    df = model["df"]
    scale = model["spread"] * math.sqrt(model["b_n"] / model["a_n"] * (1 + x @ vn @ x))
    value = model["mean"] + model["spread"] * float(x @ mn)
    tol = max(TOLERANCE * abs(value), TOLERANCE * model["range_seen"])
    low, high = stats.t.interval(RANGE, df, loc=value, scale=scale)
    return {
        "guess": value, "low": low, "high": high, "scale": scale, "df": df, "tolerance": tol,
        "confidence": float(2 * stats.t.cdf(tol / scale, df) - 1),
        "typical_error": float(scale * stats.t.ppf(0.75, df)),  # half the time closer than this
        "method": model["method"],
    }


def formula_catalog(table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The descriptors a formula can use: numbers that change between runs, with the duplicates
    merged as in the models ({"key", "aliases"}), sorted by name."""
    columns = _columns(table)
    groups = _merge_duplicates(columns, _knobs(columns, len(table)))
    found = [{"key": rep, "aliases": keys} for rep, keys in groups.items()
             if columns[rep]["numeric"] and np.ptp(columns[rep]["values"]) > 0]
    return sorted(found, key=lambda e: pretty_name(e["key"].partition(":")[2]))


def formula(table: list[dict[str, Any]], target: str, predictors: list[str]) -> dict[str, Any]:
    """How `target` follows from `predictors`, fitted to every run that has all of them: the same
    Bayesian linear regression as the models, asked about any descriptors. For the Ghosts tab."""
    columns = _columns(table)
    missing = [k for k in [target, *predictors] if k not in columns or not columns[k]["numeric"]]
    if missing:
        return {"error": "only numbers can be put in a formula: " + ", ".join(pretty_name(k.partition(":")[2]) for k in missing)}
    rows = sorted(set.intersection(*(set(columns[k]["rows"]) for k in [target, *predictors])))
    if len(rows) < 2:
        return {"error": "fewer than 2 runs have all of these"}

    def at(key: str) -> np.ndarray:
        col = columns[key]
        return np.array([col["values"][col["rows"].index(i)] for i in rows])

    y = at(target)
    if np.ptp(y) == 0:
        return {"error": f"{pretty_name(target.partition(':')[2])} is the same in every run: nothing to explain"}
    x_raw = np.column_stack([at(k) for k in predictors])
    return _regress(y, x_raw, predictors) | {
        "target": target, "n": len(rows), "sims": [table[i]["id"] for i in rows],
        "units": columns[target]["units"], "predictor_units": {k: columns[k]["units"] for k in predictors},
        "inputs": {k: at(k).tolist() for k in predictors},
    }


# ---------------------------------------------------------------------------- relationships (ghost.md 5.3)


def _relationships(groups, columns, knobs, sims) -> list[dict[str, Any]]:
    numeric = [rep for rep in groups if columns[rep]["numeric"] and np.ptp(columns[rep]["values"]) > 0]
    found = []
    for a, b in itertools.combinations(numeric, 2):
        if a in knobs and b in knobs:
            continue  # knobs are set by design, not related by nature
        ca, cb = columns[a], columns[b]
        shared = sorted(set(ca["rows"]) & set(cb["rows"]))
        va = np.array([ca["values"][ca["rows"].index(i)] for i in shared])
        vb = np.array([cb["values"][cb["rows"].index(i)] for i in shared])
        n = len(shared)
        rel = {
            "id": "rel-" + hashlib.sha1(f"{a}|{b}".encode()).hexdigest()[:12],
            "name": f"{pretty_name(a.partition(':')[2])} ↔ {pretty_name(b.partition(':')[2])}",
            "a": a, "b": b, "a_keys": groups[a], "b_keys": groups[b],
            "kind": "cause" if (a in knobs or b in knobs) else "association",
            "n": n, "points": [[sims[i], float(x), float(y)] for i, x, y in zip(shared, va, vb)],
            "a_units": ca["units"], "b_units": cb["units"],
        }
        if n >= 2 and np.ptp(va) > 0 and np.ptp(vb) > 0:
            rel["r"] = float(np.clip(np.corrcoef(va, vb)[0, 1], -1, 1))
        if n >= 3 and "r" in rel:
            r = float(np.clip(rel["r"], -0.999999, 0.999999))
            df = n - 2
            t = r * math.sqrt(df) / math.sqrt(1 - r * r)
            p = float(2 * stats.t.sf(abs(t), df))
            rel |= {"t": t, "df": df, "p": p, "z": float(math.copysign(stats.norm.isf(p / 2), r))}
        found.append(rel)

    tested = [r for r in found if "z" in r]
    for rel in found:
        if "z" not in rel:
            rel |= {"confidence": 0.0, "note": "needs at least 3 runs with both values changing to be tested"}
    if not tested:
        return found
    z = np.array([r["z"] for r in tested])
    null = {"null": "textbook", "null_mean": 0.0, "null_sd": 1.0, "pi0": 1.0}
    lfdr = None
    if len(tested) >= MIN_PAIRS_FOR_EMPIRICAL_NULL:
        try:
            nd = NullDistribution(z, estimate_null_proportion=True)
            lfdr = local_fdr(z, null_proportion=nd.null_proportion, null_pdf=nd.pdf)
            null = {"null": "empirical", "null_mean": float(nd.mean), "null_sd": float(nd.sd),
                    "pi0": float(nd.null_proportion)}
        except Exception:  # noqa: BLE001 - fall back to the cautious textbook null
            lfdr = None
    if lfdr is None:
        lfdr = local_fdr(z)
    for rel, value in zip(tested, np.clip(lfdr, 0, 1)):
        rel |= null | {"lfdr": float(value), "confidence": float(1 - value), "pairs_tested": len(tested)}
    return found


# ---------------------------------------------------------------------------- ghosts (ghost.md 4.3, 5.2)


def _ghosts(models, columns, knobs, sims, count, titles) -> list[dict[str, Any]]:
    """ghost.md 4.3: every ghost is one of the real runs with exactly one of its knobs moved to a new
    value, drawn at random from the range that knob has been tried over (seeded by the run and the
    knob, so a ghost keeps its value from one recalculation to the next). Every other knob stays as in
    that run, and the ghost guesses exactly the descriptors that run has. `count` ghosts per run for
    each of its knobs."""
    if not knobs or count <= 0:
        return []
    ghosts = []
    for i, base in enumerate(sims):
        own = [k for k in knobs if i in columns[k]["at"]]
        if not own:
            continue  # a run without knobs has nothing to move
        here = np.array([_value(columns, k, i) for k in own], dtype=float)
        alike = [r for r in range(len(sims)) if all(r in columns[k]["at"] for k in own)]
        tried = {tuple(np.round([_value(columns, k, r) for k in own], 9)) for r in alike}
        descriptors = {m["descriptor"]: m for m in models.values() if base in m["sims"]}  # its family's
        for j, knob in enumerate(own):
            values = columns[knob]["values"]
            lo, hi = float(np.min(values)), float(np.max(values))
            span = hi - lo
            whole = bool(np.all(np.mod(values, 1) == 0))
            step = 10.0 ** (math.floor(math.log10(span)) - 1)
            step = max(step, 1.0) if whole else step
            rng = np.random.default_rng(int(hashlib.sha1(f"{base}|{knob}".encode()).hexdigest()[:8], 16))
            for _ in range(count):
                for _attempt in range(100):
                    value = float(np.clip(round(rng.uniform(lo, hi) / step) * step, lo, hi))
                    row = here.copy()
                    row[j] = value
                    if abs(value - here[j]) >= MIN_MOVE * span and tuple(np.round(row, 9)) not in tried:
                        break
                else:
                    continue  # no untried value left for this knob here
                tried.add(tuple(np.round(row, 9)))
                ghosts.append(_ghost(descriptors, columns, own, row, base, knob, float(here[j]),
                                     titles.get(base, base), len(sims)))
    return ghosts


def _ghost(models, columns, knobs, row, base, knob, was, base_title, n_sims) -> dict[str, Any]:
    values = dict(zip(knobs, row))
    guesses = {key: guess(m, values) | {"units": m["units"]} for key, m in models.items()}
    confidences = {k: g["confidence"] for k, g in guesses.items()}
    weakest = min(confidences, key=confidences.get) if confidences else None
    inputs = {k: {"value": _tidy(v), "units": columns[k]["units"]} | ({"was": _tidy(was)} if k == knob else {})
              for k, v in values.items()}
    units = columns[knob]["units"]
    return {
        "id": "calc-" + hashlib.sha1(f"{base}|{knob}|{_tidy(values[knob])}".encode()).hexdigest()[:12],
        "title": f"{short_title(base_title, 24)} · {_knob_name(knob)} {_tidy(values[knob]):g}{' ' + units if units else ''}",
        "based_on": [base], "base": base, "changed": knob,
        "model": "Bayesian linear regression + rule of succession",
        "confidence": float(np.mean(list(confidences.values()))) if confidences else 0.0,
        "weakest": weakest, "weakest_confidence": confidences.get(weakest),
        "values_guessed": len(guesses), "sims_used": n_sims,
        "inputs_json": json.dumps(inputs),
        # full precision: a guess is checked against the real run exactly as it was made
        "predicted_json": json.dumps({k: _plain(g) for k, g in guesses.items()}),
    }


# ---------------------------------------------------------------------------- record (ghost.md 8)


def _write_record(table: list[dict[str, Any]], result: dict[str, Any]) -> None:
    RECORDS.mkdir(parents=True, exist_ok=True)
    record = {
        "calc_id": result["calc_id"], "method": METHOD, "method_version": METHOD_VERSION,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "libraries": {"numpy": np.__version__, "scipy": _version("scipy"), "statsmodels": _version("statsmodels")},
        "settings": {"prior_slope_var": PRIOR_SLOPE_VAR, "prior_intercept_var": PRIOR_INTERCEPT_VAR,
                     "prior_a": PRIOR_A, "prior_b": PRIOR_B, "tolerance": TOLERANCE, "range": RANGE,
                     "min_pairs_for_empirical_null": MIN_PAIRS_FOR_EMPIRICAL_NULL},
        "simulations": [{"id": s["id"], "fingerprint": s.get("fingerprint")} for s in table],
        "knobs": result["knobs"], "merged": {k: v for k, v in result["groups"].items() if len(v) > 1},
        "models": result["models"], "relationships": result["relationships"], "ghosts": result["ghosts"],
    }
    (RECORDS / f"{result['calc_id']}.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")


def _version(module: str) -> str:
    return __import__(module).__version__


# ---------------------------------------------------------------------------- small helpers


def _plain(g: dict[str, Any]) -> dict[str, Any]:
    """numpy numbers as plain floats, not rounded."""
    return {k: (float(v) if isinstance(v, (float, np.floating)) else v) for k, v in g.items()}


def _clean(g: dict[str, Any]) -> dict[str, Any]:
    return {k: (_tidy(v) if isinstance(v, float) else v) for k, v in g.items()}


def _tidy(value: float) -> float:
    return float(f"{float(value):.6g}")


def _knob_name(key: str) -> str:
    pretty = pretty_name(key.partition(":")[2])
    return pretty[len("Requested "):] if pretty.startswith("Requested ") else pretty
