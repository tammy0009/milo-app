"""The numpy force layout (ui/layout.py) moves every node exactly as the plain-Python layout it
replaced, on the real graph in Neo4j: every run, and every descriptor value as its own node (all
fields ticked), linked the way the canvas links them, with each campaign's runs pulled together.
Start positions are random, as the canvas places new nodes. Skipped when Neo4j is not up."""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from milo_app import graph
from milo_app.ui import layout
from milo_app.ui.format import pretty_name, quantity

TOLERANCE = 1e-9  # px: float sums in a different order, nothing more

# The layout's constants as they were at f92fb66, frozen here so the reference cannot follow a change.
REPULSION, SPRING, SPRING_LENGTH, GRAVITY, CAMPAIGN_PULL = 42000.0, 0.035, 170.0, 0.004, 0.02
DAMPING, MAX_STEP, COOLING, ALPHA_MIN = 0.82, 30.0, 0.985, 0.01


def reference_step(xs, ys, vxs, vys, held, edges, groups, a):
    """The canvas's layout tick before numpy (graph_view.py at f92fb66), unchanged except that it
    reads and writes lists instead of Qt items."""
    n = len(xs)
    fx, fy = [0.0] * n, [0.0] * n
    for i in range(n):
        xi, yi = xs[i], ys[i]
        for j in range(i + 1, n):
            dx, dy = xi - xs[j], yi - ys[j]
            d2 = dx * dx + dy * dy
            if d2 > 1_440_000:
                continue
            if d2 < 1.0:
                dx, dy, d2 = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
            d = math.sqrt(d2)
            f = REPULSION / d2
            px, py = f * dx / d, f * dy / d
            fx[i] += px
            fy[i] += py
            fx[j] -= px
            fy[j] -= py
    for members in groups:
        if len(members) > 1:
            cx = sum(xs[i] for i in members) / len(members)
            cy = sum(ys[i] for i in members) / len(members)
            for i in members:
                fx[i] += CAMPAIGN_PULL * (cx - xs[i])
                fy[i] += CAMPAIGN_PULL * (cy - ys[i])
    for i, j in edges:
        dx, dy = xs[j] - xs[i], ys[j] - ys[i]
        d = math.hypot(dx, dy) or 1.0
        f = SPRING * (d - SPRING_LENGTH)
        px, py = f * dx / d, f * dy / d
        fx[i] += px
        fy[i] += py
        fx[j] -= px
        fy[j] -= py
    xs, ys, vxs, vys = list(xs), list(ys), list(vxs), list(vys)
    for i in range(n):
        if held[i]:
            vxs[i] = vys[i] = 0.0
            continue
        vxs[i] = (vxs[i] + a * (fx[i] - GRAVITY * xs[i])) * DAMPING
        vys[i] = (vys[i] + a * (fy[i] - GRAVITY * ys[i])) * DAMPING
        step = math.hypot(vxs[i], vys[i])
        if step > MAX_STEP:
            vxs[i], vys[i] = vxs[i] * MAX_STEP / step, vys[i] * MAX_STEP / step
        xs[i], ys[i] = xs[i] + vxs[i], ys[i] + vys[i]
    return xs, ys, vxs, vys


@pytest.fixture(scope="module")
def real_graph():
    """(node count, links as index pairs, campaign groups, descriptor groups per node) from Neo4j."""
    driver = graph.connect()
    if not graph.is_ready(driver):
        pytest.skip("Neo4j is not up")
    sims = graph.list_simulations(driver)
    if not sims:
        pytest.skip("the graph has no runs")
    ids = [s["bundle_id"] for s in sims]
    field_of = [None] * len(ids)
    links: list[tuple[int, int]] = []
    for group in graph.descriptor_groups(driver):
        name = group["key"].partition(":")[2]
        made: dict[str, int] = {}
        for value_json, units, bundle_id in graph.descriptor_links(driver, group["key"]):
            label = f"{pretty_name(name)} = {quantity(value_json, units)}"
            if label not in made:
                made[label] = len(ids)
                ids.append(f"{group['key']}={label}")
                field_of.append(group["key"])
            links.append((made[label], ids.index(bundle_id)))
    campaigns: dict[str, list[int]] = {}
    for i, s in enumerate(sims):
        if graph.campaign_of(s):
            campaigns.setdefault(graph.campaign_of(s), []).append(i)
    driver.close()
    return {"n": len(ids), "sims": len(sims), "links": links, "groups": list(campaigns.values()), "field": field_of}


def pick(g, fields: int):
    """The runs plus the descriptor nodes of the first `fields` fields: what the canvas shows when
    those fields are ticked."""
    keys = list(dict.fromkeys(f for f in g["field"] if f))[:fields]
    keep = [i for i in range(g["n"]) if i < g["sims"] or g["field"][i] in keys]
    new = {old: k for k, old in enumerate(keep)}
    links = [(new[a], new[b]) for a, b in g["links"] if a in new and b in new]
    return len(keep), links, [[new[i] for i in grp] for grp in g["groups"]]


def start(n: int, seed: int):
    rng = random.Random(seed)
    spread = 140 * math.sqrt(n)
    return [rng.uniform(-spread, spread) for _ in range(n)], [rng.uniform(-spread, spread) for _ in range(n)]


def both(xs, ys, vxs, vys, held, links, groups, alpha, seed):
    random.seed(seed)
    ref = reference_step(xs, ys, vxs, vys, held, links, groups, alpha)
    random.seed(seed)
    new = layout.step(np.array(xs, float), np.array(ys, float), np.array(vxs, float), np.array(vys, float),
                      np.array(held, bool), np.array(links, int).reshape(-1, 2),
                      [np.array(g, int) for g in groups], alpha)
    return ref, new


def worst(ref, new) -> float:
    return max(float(np.max(np.abs(np.array(r) - n))) for r, n in zip(ref, new))


def test_every_node_every_field_one_tick_at_a_time(real_graph):
    """The whole real graph, every field ticked: each tick moves every node the same."""
    n, links, groups = real_graph["n"], real_graph["links"], real_graph["groups"]
    xs, ys = start(n, 1)
    vxs, vys, held = [0.0] * n, [0.0] * n, [False] * n
    alpha = 1.0
    for tick in range(3):
        ref, new = both(xs, ys, vxs, vys, held, links, groups, alpha, tick)
        assert worst(ref, new) < TOLERANCE, f"tick {tick}"
        xs, ys, vxs, vys = ref
        alpha *= COOLING


def test_whole_settle_matches(real_graph):
    """Ticked fields as you might have them (500+ nodes), through a whole settle until the layout
    rests: at every tick both layouts start from the same positions and must land in the same place.
    (Run side by side each on its own positions they drift apart, but so does the old layout from
    itself: the settle is chaotic, and a start moved by 1e-12 px ends up hundreds of px away.)"""
    n, links, groups = pick(real_graph, 300)
    xs, ys = start(n, 2)
    state = (xs, ys, [0.0] * n, [0.0] * n)
    held = [False] * n
    alpha, ticks = 1.0, 0
    while alpha >= ALPHA_MIN:
        ref, new = both(*state, held, links, groups, alpha, ticks)
        assert worst(ref, new) < TOLERANCE, f"tick {ticks}"
        state = ref
        alpha *= COOLING
        ticks += 1
    assert ticks > 300 and n > 500


def test_constants_unchanged():
    """The layout still uses the constants it had, so matching the reference means matching the old
    layout (and the test above would notice any change to them: see test_a_real_change_is_caught)."""
    assert (layout.REPULSION, layout.SPRING, layout.SPRING_LENGTH, layout.GRAVITY, layout.CAMPAIGN_PULL,
            layout.DAMPING, layout.MAX_STEP, layout.COOLING, layout.ALPHA_MIN, layout.REACH2) == (
        REPULSION, SPRING, SPRING_LENGTH, GRAVITY, CAMPAIGN_PULL, DAMPING, MAX_STEP, COOLING, ALPHA_MIN, 1_440_000)


def test_a_real_change_is_caught(real_graph, monkeypatch):
    """The comparison is sharp enough to matter: a 0.1 % change to the push shows up far above the
    tolerance."""
    monkeypatch.setattr(layout, "REPULSION", REPULSION * 1.001)
    n, links, groups = pick(real_graph, 60)
    xs, ys = start(n, 4)
    ref, new = both(xs, ys, [0.0] * n, [0.0] * n, [False] * n, links, groups, 1.0, 0)
    assert worst(ref, new) > 1e-4


def test_dragged_node_and_nodes_on_top_of_each_other(real_graph):
    """A node being dragged stays put; nodes on the very same spot (a descriptor dropped on its
    run) get the same random nudge apart in both."""
    n, links, groups = pick(real_graph, 12)
    xs, ys = start(n, 3)
    a, b = links[0]
    xs[a], ys[a] = xs[b], ys[b]
    xs[a + 1], ys[a + 1] = xs[b], ys[b]
    held = [False] * n
    held[0] = True
    ref, new = both(xs, ys, [5.0] * n, [-5.0] * n, held, links, groups, 1.0, 7)
    assert worst(ref, new) < TOLERANCE
    assert (new[0][0], new[1][0], new[2][0], new[3][0]) == (xs[0], ys[0], 0.0, 0.0)
