"""The graph canvas's force layout, one step at a time, in numpy (no Qt): nodes push each other
apart, links pull their ends together like springs, each campaign's runs and ghosts are pulled
toward their middle, and a light gravity keeps everything near the origin. Forces are scaled by
`alpha`, which the canvas cools each tick; the layout rests below ALPHA_MIN.
"""
from __future__ import annotations

import random

import numpy as np
from scipy.spatial import cKDTree

REPULSION = 42000.0
SPRING = 0.035
SPRING_LENGTH = 170.0
GRAVITY = 0.004
CAMPAIGN_PULL = 0.02  # toward the middle of a campaign's runs and ghosts
DAMPING = 0.82
MAX_STEP = 30.0
COOLING = 0.985
ALPHA_MIN = 0.01
TICK_MS = 16
REACH = 1200.0  # px: beyond this the push is negligible
REACH2 = REACH * REACH


def step(x: np.ndarray, y: np.ndarray, vx: np.ndarray, vy: np.ndarray, held: np.ndarray,
         edges: np.ndarray, groups: list[np.ndarray], alpha: float) -> tuple[np.ndarray, ...]:
    """One tick. x, y, vx, vy: float arrays per node; held: bool per node (being dragged: it stays
    put and its velocity is zeroed); edges: int array (m, 2) of node indices; groups: an index array
    per campaign ring. Returns the new (x, y, vx, vy)."""
    n = len(x)
    fx, fy = np.zeros(n), np.zeros(n)

    # Push: every pair within reach (found with a k-d tree, so far-apart pairs cost nothing). Pairs
    # closer than 1 px get a random nudge instead, drawn from `random` pair by pair in (i, j) order,
    # so a seeded run is repeatable.
    pairs = cKDTree(np.column_stack([x, y])).query_pairs(REACH * (1 + 1e-9), output_type="ndarray")
    i, j = pairs[:, 0], pairs[:, 1]
    dx, dy = x[i] - x[j], y[i] - y[j]
    d2 = dx * dx + dy * dy
    near = (d2 <= REACH2) & (d2 >= 1.0)  # the tree searches a hair wider; this is the exact cut
    push = REPULSION / (d2[near] * np.sqrt(d2[near]))  # f / d, with f = REPULSION / d2
    px, py = push * dx[near], push * dy[near]
    fx += np.bincount(i[near], px, n) - np.bincount(j[near], px, n)
    fy += np.bincount(i[near], py, n) - np.bincount(j[near], py, n)
    close = pairs[d2 < 1.0]
    for a, b in sorted(map(tuple, close.tolist())):
        ndx, ndy = random.uniform(-1, 1), random.uniform(-1, 1)
        fx[a] += REPULSION * ndx
        fy[a] += REPULSION * ndy
        fx[b] -= REPULSION * ndx
        fy[b] -= REPULSION * ndy

    # each campaign's runs and ghosts are pulled gently together, so its ring stays tight
    for members in groups:
        if len(members) > 1:
            np.add.at(fx, members, CAMPAIGN_PULL * (x[members].mean() - x[members]))
            np.add.at(fy, members, CAMPAIGN_PULL * (y[members].mean() - y[members]))

    if len(edges):
        a, b = edges[:, 0], edges[:, 1]
        dx, dy = x[b] - x[a], y[b] - y[a]
        d = np.hypot(dx, dy)
        d[d == 0] = 1.0
        f = SPRING * (d - SPRING_LENGTH) / d
        np.add.at(fx, a, f * dx)
        np.add.at(fy, a, f * dy)
        np.subtract.at(fx, b, f * dx)
        np.subtract.at(fy, b, f * dy)

    vx = (vx + alpha * (fx - GRAVITY * x)) * DAMPING
    vy = (vy + alpha * (fy - GRAVITY * y)) * DAMPING
    speed = np.hypot(vx, vy)
    fast = speed > MAX_STEP
    vx[fast] *= MAX_STEP / speed[fast]
    vy[fast] *= MAX_STEP / speed[fast]
    vx[held] = vy[held] = 0.0
    return np.where(held, x, x + vx), np.where(held, y, y + vy), vx, vy
