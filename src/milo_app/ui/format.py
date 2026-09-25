"""Turning stored values into text for the screen."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any


def elapsed(sim: dict[str, Any]) -> str:
    value, units = sim.get("time_elapsed"), sim.get("time_elapsed_units") or "s"
    if not isinstance(value, (int, float)):
        return "" if value is None else str(value)
    if units == "s" and value >= 60:
        h, rest = divmod(int(round(value)), 3600)
        m, s = divmod(rest, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
    return f"{value:g} {units}"


def landed(sim: dict[str, Any]) -> str:
    """When a run landed, for sorting: its finish (bundle.json is written last, so that is when the
    bundle appeared), else its start, else when MILO took it in."""
    return str(sim.get("finish") or sim.get("start") or sim.get("ingested_at") or "")


def when(stamp: str) -> str:
    """An ISO time as it reads here, in local time: "Sep 24, 9:11 PM"."""
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return stamp or ""
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    hour = moment.hour % 12 or 12
    return f"{moment:%b} {moment.day}, {hour}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


def shown_value(row: dict[str, Any]) -> tuple[str, str]:
    """(text for the cell, full text for the tooltip)."""
    try:
        value = json.loads(row.get("value_json", "null"))
    except ValueError:
        value = row.get("value_json")
    full = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = full if len(full) <= 120 else full[:117] + "…"
    return text, full[:4000]


def size_text(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def quantity(value_json: str, units: str | None) -> str:
    """A value with its units, as a person would say it: 13620 s -> "3.78 hours"."""
    try:
        value = json.loads(value_json)
    except ValueError:
        return value_json
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        if units in ("s", "sec", "seconds") and abs(value) >= 60:
            value, units = (value / 3600, "hours") if abs(value) >= 3600 else (value / 60, "min")
            text = f"{value:.2f}"
        else:
            text = f"{value:.6g}"
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    if len(text) > 48:
        text = text[:45] + "…"
    return f"{text} {units}" if units else text


# ---------------------------------------------------------------------------- field names
#
# Script names read as plain English: "requested.pla_mass_fraction" -> "Requested PLA Mass Fraction".
# Add to these two tables to teach it more words.

ACRONYMS = {
    "pla": "PLA", "pcl": "PCL", "npt": "NPT", "nvt": "NVT", "nve": "NVE", "cpu": "CPU", "gpu": "GPU",
    "id": "ID", "md": "MD", "dft": "DFT", "rms": "RMS", "xsd": "XSD", "milo": "MILO", "vdw": "vdW",
    "tg": "Tg", "ff": "FF",
}
# A last word that is really a unit becomes one in brackets: "temperature_k" -> "Temperature (K)".
UNIT_WORDS = {"k": "(K)", "gpa": "(GPa)", "mpa": "(MPa)", "min": "(min)", "s": "(s)", "ps": "(ps)", "fs": "(fs)"}


def pretty_name(raw: str) -> str:
    text = raw.replace("VdW", "Vdw")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)  # CamelCase -> Camel Case
    words = [w for w in re.split(r"[._\s-]+", text) if w]
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        if i == len(words) - 1 and i > 0 and low in UNIT_WORDS:
            out.append(UNIT_WORDS[low])
        elif low in ACRONYMS:
            out.append(ACRONYMS[low])
        elif word.islower():
            out.append(word.capitalize())
        else:
            out.append(word)
    return " ".join(out) or raw


def short_title(title: str, limit: int = 18) -> str:
    """A label short enough to sit under a node: the part before any ':' and only as many whole
    words as fit. "PLA30/PCL70 amorphous cell" -> "PLA30/PCL70…"."""
    head = title.split(":", 1)[0].strip() or title
    if len(head) <= limit:
        return head
    words, out = head.split(), ""
    for word in words:
        if len(out) + len(word) + (1 if out else 0) > limit:
            break
        out = f"{out} {word}" if out else word
    return (out or head[:limit]).rstrip(" ,;/-") + "…"


def sig(value: float, digits: int = 4) -> str:
    """A number to `digits` significant figures, written the way people write numbers:
    16470 -> "16,470", 0.0342 -> "0.0342". Only very large or tiny ones use powers of ten."""
    if not isinstance(value, (int, float)) or value == 0 or value != value or abs(value) == float("inf"):
        return f"{value:g}" if isinstance(value, (int, float)) else str(value)
    magnitude = math.floor(math.log10(abs(value)))
    if -4 <= magnitude < 7:
        text = f"{value:,.{max(digits - 1 - magnitude, 0)}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text
    return f"{value:.{digits - 1}e}"


SUPERSCRIPTS = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def units_text(units: object) -> str:
    """Units as they are written: "A^3" -> "A³", "g/cm^3" -> "g/cm³"."""
    if not units:
        return ""
    return re.sub(r"\^(-?\d+)", lambda m: m.group(1).translate(SUPERSCRIPTS), str(units))


def prior_text(prior: object) -> str:
    """What each regression prior means, for the panels (ghost.md 4.1)."""
    if prior == "noninformative":
        return ("standard noninformative prior, p(slopes, σ²) ∝ 1/σ² (Bayesian Data Analysis 14.2):\n"
                "enough runs to measure the scatter from the data alone")
    return ("weakly informative conjugate prior, on each descriptor's own scale:\n"
            "slope ~ Normal(0, σ²), σ² ~ Inverse-Gamma(1, 1); used while there are too few runs\n"
            "to measure the scatter, so ranges stay wide and confidence low")


def test_summary(test: dict | None) -> str:
    """A blind test in words (blind.py; ghost.md 7.1). Empty when the run was never tested."""
    if not test:
        return ""
    if not test.get("testable"):
        return f"Not blind-tested: {test.get('reason', 'nothing to predict it from')}."
    stated = test.get("stated")
    lines = [
        f"Predicted blind, from the {test.get('runs_before')} runs before it:",
        f"{test.get('right')} of {test.get('values')} values came out right (within 5 %)"
        + (f"; it expected {stated:.0%}" if isinstance(stated, (int, float)) else ""),
    ]
    if test.get("ranged"):
        lines.append(f"{test.get('inside')} of {test.get('ranged')} numbers landed inside their 90 % ranges")
    never = test.get("never_varied")
    if isinstance(never, str):
        try:
            never = json.loads(never)
        except ValueError:
            never = []
    for item in never or []:
        lines.append(f"⚠ {pretty_name(item['key'].partition(':')[2])} changed to {item['now']:g}, but was "
                     f"{item['before']:g} in every earlier run: the model had never seen it vary")
    for key in test.get("outside_tried") or []:
        lines.append(f"⚠ {pretty_name(key.partition(':')[2])} is outside the range tried before")
    return "\n".join(lines)
