"""The Neo4j graph. Generic on purpose: it stores what the bundle says and nothing inferred.

    (:Simulation {bundle_id, title, every bundle.json field, source_path, fingerprint, ...})
        -[:HAS_INPUT]->  (:Input  {name, order, value, value_json, units, extra_json})
        -[:HAS_OUTPUT]-> (:Output {name, order, value, value_json, units, extra_json})
        -[:HAS_FILE]->   (:File   {sector, relpath, size, sha256})

    (:Relationship {id, name, a, b, ...})   between two descriptors; a/b are descriptor group keys
    (:Prediction {id, title, ...}) -[:BASED_ON]-> (:Simulation)   a run that has not happened yet
    (:Model {id, descriptor, method, ...})   how one descriptor is guessed (see ghost.md)
    (:Test {id, ...}) -[:TESTED]-> (:Simulation)   that run predicted blind before it arrived (blind.py)
    (:Prediction {source: "verified"}) -[:VERIFIED_BY]-> (:Simulation)   a ghost that was then really run

`value` is set when Neo4j can hold it natively (numbers, text, booleans, flat lists) so it can be
queried; `value_json` always holds the exact original. Re-ingesting a bundle replaces it whole.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver, GraphDatabase

from milo_app.bundle import Bundle
from milo_app.config import get_settings

SCHEMA = (
    "CREATE CONSTRAINT simulation_id IF NOT EXISTS FOR (s:Simulation) REQUIRE s.bundle_id IS UNIQUE",
    "CREATE INDEX input_name IF NOT EXISTS FOR (n:Input) ON (n.name)",
    "CREATE INDEX output_name IF NOT EXISTS FOR (n:Output) ON (n.name)",
    "CREATE CONSTRAINT relationship_id IF NOT EXISTS FOR (r:Relationship) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT prediction_id IF NOT EXISTS FOR (p:Prediction) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT model_id IF NOT EXISTS FOR (m:Model) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT test_id IF NOT EXISTS FOR (t:Test) REQUIRE t.id IS UNIQUE",
)
ITEM_LABEL = {"INPUT": ("Input", "HAS_INPUT"), "OUTPUT": ("Output", "HAS_OUTPUT")}
# Properties the app sets itself; a bundle.json field with the same name is kept as manifest_<name>.
OWN = ("bundle_id", "title", "source_path", "fingerprint", "ingested_at", "problems",
       "input_count", "output_count", "file_count")


def connect() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password),
                                notifications_min_severity="OFF")


def is_ready(driver: Driver) -> bool:
    try:
        driver.verify_connectivity()
        return True
    except Exception:  # noqa: BLE001 - "not up yet" is the normal case while starting
        return False


def init_schema(driver: Driver) -> None:
    for statement in SCHEMA:
        driver.execute_query(statement)


def storable(value: Any) -> Any:
    """The value if Neo4j can hold it as a property, else None."""
    if isinstance(value, (bool, str, float)):
        return value
    if isinstance(value, int):
        return value if -(2**63) <= value < 2**63 else None
    if isinstance(value, list) and value:
        kinds = {type(v) for v in value}
        if len(kinds) == 1 and next(iter(kinds)) in (bool, int, float, str):
            return value
        if kinds <= {int, float}:
            return [float(v) for v in value]
    return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def title_of(bundle: Bundle) -> str:
    m = bundle.manifest
    return str(m.get("title") or m.get("task") or m.get("module") or bundle.bundle_id)


def ingest(driver: Driver, bundle: Bundle, fingerprint: str) -> None:
    props: dict[str, Any] = {}
    for key, value in bundle.manifest.items():
        name = f"manifest_{key}" if key in OWN else key
        kept = storable(value)
        props[name] = kept if kept is not None else _json(value)
    props.update(
        bundle_id=bundle.bundle_id,
        title=title_of(bundle),
        source_path=str(bundle.root),
        fingerprint=fingerprint,
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        problems=bundle.problems,
        input_count=len(bundle.items_in("INPUT")),
        output_count=len(bundle.items_in("OUTPUT")),
        file_count=len(bundle.files),
    )
    items = {sector: [] for sector in ITEM_LABEL}
    for item in bundle.items:
        node = {"name": item.name, "order": item.order, "value_json": _json(item.value)}
        if (kept := storable(item.value)) is not None:
            node["value"] = kept
        if item.units is not None:
            node["units"] = str(item.units)
        if item.extra:
            node["extra_json"] = _json(item.extra)
        items[item.sector].append(node)
    files = [{"sector": f.sector, "relpath": f.relpath, "size": f.size, "sha256": f.sha256} for f in bundle.files]

    def write(tx) -> None:
        tx.run("MERGE (s:Simulation {bundle_id: $id}) SET s = $props", id=bundle.bundle_id, props=props)
        tx.run("MATCH (s:Simulation {bundle_id: $id})-[:HAS_INPUT|HAS_OUTPUT|HAS_FILE]->(old) DETACH DELETE old",
               id=bundle.bundle_id)
        for sector, (label, rel) in ITEM_LABEL.items():
            tx.run(f"MATCH (s:Simulation {{bundle_id: $id}}) UNWIND $rows AS row "
                   f"CREATE (s)-[:{rel}]->(n:{label}) SET n = row", id=bundle.bundle_id, rows=items[sector])
        tx.run("MATCH (s:Simulation {bundle_id: $id}) UNWIND $rows AS row "
               "CREATE (s)-[:HAS_FILE]->(f:File) SET f = row", id=bundle.bundle_id, rows=files)

    with driver.session() as session:
        session.execute_write(write)


def fingerprints(driver: Driver) -> dict[str, str]:
    """source_path -> fingerprint for everything already in the graph."""
    rows, _, _ = driver.execute_query("MATCH (s:Simulation) RETURN s.source_path AS path, s.fingerprint AS fp")
    return {r["path"]: r["fp"] for r in rows if r["path"]}


def list_simulations(driver: Driver) -> list[dict[str, Any]]:
    rows, _, _ = driver.execute_query("MATCH (s:Simulation) RETURN properties(s) AS s ORDER BY s.start DESC")
    return [r["s"] for r in rows]


def get_simulation(driver: Driver, bundle_id: str) -> dict[str, Any] | None:
    rows, _, _ = driver.execute_query(
        """
        MATCH (s:Simulation {bundle_id: $id})
        RETURN properties(s) AS s,
               COLLECT { MATCH (t:Test)-[:TESTED]->(s) RETURN properties(t) } AS tests,
               COLLECT { MATCH (s)-[:HAS_INPUT]->(n)  RETURN properties(n) AS p ORDER BY n.order } AS inputs,
               COLLECT { MATCH (s)-[:HAS_OUTPUT]->(n) RETURN properties(n) AS p ORDER BY n.order } AS outputs,
               COLLECT { MATCH (s)-[:HAS_FILE]->(f)   RETURN properties(f) AS p ORDER BY f.relpath } AS files
        """,
        id=bundle_id,
    )
    if not rows:
        return None
    r = rows[0]
    return {"simulation": r["s"], "inputs": r["inputs"], "outputs": r["outputs"], "files": r["files"],
            "test": r["tests"][0] if r["tests"] else None}


# ---------------------------------------------------------------------------- descriptors
#
# A descriptor group is any field a simulation carries: a bundle.json field, an input, or an
# output. Each distinct value of that field is one descriptor node, linked to every simulation
# that has it. Worked out from the stored data when asked, so a new field in any bundle
# (e.g. "campaign") becomes a group with no code change.
#
# Group keys: "bundle:<field>", "input:<name>", "output:<name>".

SOURCES = {"input": ("HAS_INPUT", "Input"), "output": ("HAS_OUTPUT", "Output")}
# Simulation properties the app itself sets: not descriptors.
NOT_DESCRIPTORS = set(OWN) | {"manifest_bundle_id"}


def descriptor_groups(driver: Driver) -> list[dict[str, Any]]:
    """Every group with how many distinct values it has and how many simulations carry it."""
    groups: list[dict[str, Any]] = []
    fields: dict[str, tuple[set[str], int]] = {}
    for sim in list_simulations(driver):
        for key, value in sim.items():
            if key not in NOT_DESCRIPTORS:
                values, count = fields.get(key, (set(), 0))
                values.add(_json(value))
                fields[key] = (values, count + 1)
    for key in sorted(fields, key=str.lower):
        values, count = fields[key]
        groups.append({"key": f"bundle:{key}", "source": "bundle", "name": key,
                       "values": len(values), "sims": count})
    for source, (rel, label) in SOURCES.items():
        rows, _, _ = driver.execute_query(
            f"MATCH (:Simulation)-[:{rel}]->(n:{label}) WITH n.name AS name, collect(n) AS ns "
            "RETURN name, size(reduce(seen = [], x IN ns | CASE WHEN x.value_json IN seen THEN seen ELSE seen + x.value_json END)) AS values, size(ns) AS sims "
            "ORDER BY toLower(name)"
        )
        groups += [{"key": f"{source}:{r['name']}", "source": source, "name": r["name"],
                    "values": r["values"], "sims": r["sims"]} for r in rows]
    return groups


def descriptor_links(driver: Driver, key: str) -> list[tuple[str, str | None, str]]:
    """(value_json, units, bundle_id) for every simulation carrying the group's field.
    A bundle.json field's units come from its <field>_units neighbour (time_elapsed_units)."""
    source, _, name = key.partition(":")
    if source == "bundle":
        rows, _, _ = driver.execute_query(
            "MATCH (s:Simulation) WHERE s[$name] IS NOT NULL "
            "RETURN s.bundle_id AS id, s[$name] AS v, s[$name + '_units'] AS u", name=name
        )
        return [(_json(r["v"]), r["u"], r["id"]) for r in rows]
    rel, label = SOURCES[source]
    rows, _, _ = driver.execute_query(
        f"MATCH (s:Simulation)-[:{rel}]->(n:{label} {{name: $name}}) "
        "RETURN s.bundle_id AS id, n.value_json AS v, n.units AS u",
        name=name,
    )
    return [(r["v"], r["u"], r["id"]) for r in rows]


# ---------------------------------------------------------------------------- relationships & predictions
#
# Nothing in the app makes these yet: relationships will come from analysis across simulations,
# predictions from the ML. These are the doors they come in through, and what the graph reads.


def _extra(props: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if storable(v) is not None else _json(v)) for k, v in props.items()}


def put_relationship(driver: Driver, a: str, b: str, name: str, **props: Any) -> str:
    """A relationship between descriptor groups `a` and `b` (keys like "output:Tg"). Returns its id."""
    rel_id = props.pop("id", None) or str(uuid.uuid4())
    row = _extra(props) | {"id": rel_id, "a": a, "b": b, "name": name,
                           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    driver.execute_query("MERGE (r:Relationship {id: $id}) SET r = $row", id=rel_id, row=row)
    return rel_id


def put_prediction(driver: Driver, title: str, based_on: list[str], **props: Any) -> str:
    """A predicted run, linked to the simulations the prediction was made from. Returns its id."""
    pred_id = props.pop("id", None) or str(uuid.uuid4())
    row = _extra(props) | {"id": pred_id, "title": title,
                           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    driver.execute_query(
        """
        MERGE (p:Prediction {id: $id}) SET p = $row
        WITH p OPTIONAL MATCH (p)-[old:BASED_ON]->() DELETE old
        WITH DISTINCT p UNWIND $based_on AS bundle_id
        MATCH (s:Simulation {bundle_id: bundle_id}) MERGE (p)-[:BASED_ON]->(s)
        """,
        id=pred_id, row=row, based_on=based_on,
    )
    return pred_id


def replace_predictions(driver: Driver, source: str, predictions: list[dict[str, Any]]) -> None:
    """Make the `source` predictions exactly these: write each (by its id, so a ghost that is
    still predicted keeps its place on the graph) and delete the ones no longer made."""
    keep = [p["id"] for p in predictions]
    for pred in predictions:
        pred = dict(pred)
        put_prediction(driver, pred.pop("title"), pred.pop("based_on"), source=source, **pred)
    driver.execute_query("MATCH (p:Prediction {source: $source}) WHERE NOT p.id IN $keep DETACH DELETE p",
                         source=source, keep=keep)


def replace_nodes(driver: Driver, label: str, source: str, rows: list[dict[str, Any]]) -> None:
    """Make the `label` nodes from `source` exactly these rows (written in one batch, by id).
    Used for calculated relationships and models, which are rebuilt on every recalculation."""
    rows = [_extra(r) | {"source": source} for r in rows]
    driver.execute_query(
        f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n = row "
        f"WITH collect(row.id) AS keep MATCH (old:{label} {{source: $source}}) WHERE NOT old.id IN keep DETACH DELETE old",
        rows=rows, source=source,
    )
    if not rows:
        driver.execute_query(f"MATCH (old:{label} {{source: $source}}) DETACH DELETE old", source=source)


def list_models(driver: Driver) -> list[dict[str, Any]]:
    rows, _, _ = driver.execute_query("MATCH (m:Model) RETURN properties(m) AS m")
    return [r["m"] for r in rows]


def descriptor_table(driver: Driver) -> list[dict[str, Any]]:
    """Every simulation's descriptors, keyed like the sidebar's groups ("input:<name>",
    "output:<name>", "bundle:<field>"): [{"id", "fingerprint", "values": {key: (value, units)}}].
    Numbers come back as floats, text and true/false as text; lists and tables are left out."""
    rows, _, _ = driver.execute_query(
        """
        MATCH (s:Simulation)
        RETURN s.bundle_id AS id, properties(s) AS props,
               COLLECT { MATCH (s)-[:HAS_INPUT]->(n) RETURN [n.name, n.value_json, n.units] } AS inputs,
               COLLECT { MATCH (s)-[:HAS_OUTPUT]->(n) RETURN [n.name, n.value_json, n.units] } AS outputs
        ORDER BY s.start
        """
    )
    table = []
    for r in rows:
        values: dict[str, tuple[Any, str | None]] = {}
        for prefix, items in (("input", r["inputs"]), ("output", r["outputs"])):
            for name, value_json, units in items:
                value = _descriptor_value(value_json)
                if value is not None:
                    values[f"{prefix}:{name}"] = (value, units)
        props = r["props"]
        for key, value in props.items():
            if key in NOT_DESCRIPTORS or key.endswith("_units") and key[:-6] in props:
                continue
            value = _descriptor_value(_json(value))
            if value is not None:
                values[f"bundle:{key}"] = (value, props.get(f"{key}_units"))
        table.append({"id": r["id"], "title": props.get("title"), "fingerprint": props.get("fingerprint"),
                      "values": values})
    return table


def _descriptor_value(value_json: str) -> Any:
    try:
        value = json.loads(value_json)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # BIOVIA hands some results over as its own number types, which scripts may record as text
        # ("1296.82"): a text that is exactly a finite number is that number.
        try:
            number = float(value.strip())
        except ValueError:
            return value
        return number if math.isfinite(number) and value.strip() == value else value
    return None


def numeric_table(driver: Driver) -> list[dict[str, Any]]:
    """Every simulation's numeric inputs and outputs, for calculations:
    [{"id", "inputs": {name: (value, units)}, "outputs": {name: (value, units)}}].
    Numeric bundle.json fields (time_elapsed) count as outputs, named "bundle:<field>"."""
    number = "valueType(n.value) STARTS WITH 'INTEGER' OR valueType(n.value) STARTS WITH 'FLOAT'"
    rows, _, _ = driver.execute_query(
        f"""
        MATCH (s:Simulation)
        RETURN s.bundle_id AS id, properties(s) AS props,
               COLLECT {{ MATCH (s)-[:HAS_INPUT]->(n) WHERE {number} RETURN [n.name, n.value, n.units] }} AS inputs,
               COLLECT {{ MATCH (s)-[:HAS_OUTPUT]->(n) WHERE {number} RETURN [n.name, n.value, n.units] }} AS outputs
        """
    )
    table = []
    for r in rows:
        outputs = {name: (float(value), units) for name, value, units in r["outputs"]}
        props = r["props"]
        for key, value in props.items():
            if key not in NOT_DESCRIPTORS and isinstance(value, (int, float)) and not isinstance(value, bool):
                outputs[f"bundle:{key}"] = (float(value), props.get(f"{key}_units"))
        table.append({"id": r["id"], "inputs": {name: (float(value), units) for name, value, units in r["inputs"]},
                      "outputs": outputs})
    return table


def put_test(driver: Driver, test: dict[str, Any]) -> None:
    """A blind test of one run (blind.py), linked to that run."""
    driver.execute_query(
        "MERGE (t:Test {id: $id}) SET t = $row WITH t MATCH (s:Simulation {bundle_id: $bundle_id}) MERGE (t)-[:TESTED]->(s)",
        id=test["id"], row=_extra(test), bundle_id=test["bundle_id"],
    )


def list_tests(driver: Driver) -> list[dict[str, Any]]:
    rows, _, _ = driver.execute_query("MATCH (t:Test) RETURN properties(t) AS t ORDER BY t.made_at")
    return [r["t"] for r in rows]


def verify_prediction(driver: Driver, prediction_id: str, bundle_id: str, result: dict[str, Any]) -> None:
    """A ghost that was then really run: kept as history (never recalculated away), with how it did."""
    driver.execute_query(
        """
        MATCH (p:Prediction {id: $id})
        SET p += $result, p.made_by = coalesce(p.made_by, p.source), p.source = 'verified',
            p.status = 'verified', p.verified_by = $bundle_id
        WITH p MATCH (s:Simulation {bundle_id: $bundle_id}) MERGE (p)-[:VERIFIED_BY]->(s)
        """,
        id=prediction_id, bundle_id=bundle_id, result=_extra(result),
    )


def list_relationships(driver: Driver) -> list[dict[str, Any]]:
    rows, _, _ = driver.execute_query("MATCH (r:Relationship) RETURN properties(r) AS r ORDER BY r.name")
    return [r["r"] for r in rows]


def list_predictions(driver: Driver) -> list[dict[str, Any]]:
    rows, _, _ = driver.execute_query(
        "MATCH (p:Prediction) RETURN properties(p) AS p, COLLECT { MATCH (p)-[:BASED_ON]->(s) RETURN s.bundle_id } AS based_on"
    )
    return [r["p"] | {"based_on": r["based_on"]} for r in rows]


def remove_node(driver: Driver, node_id: str) -> None:
    """Delete a Relationship or Prediction by id."""
    driver.execute_query("MATCH (n) WHERE (n:Relationship OR n:Prediction) AND n.id = $id DETACH DELETE n", id=node_id)


def remove(driver: Driver, bundle_id: str) -> None:
    driver.execute_query(
        "MATCH (s:Simulation {bundle_id: $id}) OPTIONAL MATCH (s)-[:HAS_INPUT|HAS_OUTPUT|HAS_FILE]->(n) "
        "DETACH DELETE s, n",
        id=bundle_id,
    )
