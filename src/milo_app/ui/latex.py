"""Formulas typeset like LaTeX, for the Ghosts tab.

lines() writes a fitted formula as LaTeX (each coefficient with its ± uncertainty); render() draws it
with matplotlib's built-in mathtext in the Computer Modern look, so no LaTeX installation is needed.
The same LaTeX source is what "Copy LaTeX" puts on the clipboard, so it pastes straight into a paper.

Two ways of writing it: with the descriptors' names, or with symbols (V, T, ρ, φ …, see SYMBOLS),
which is one line, centred and fitted to the page, with a legend saying what each symbol is.
"""
from __future__ import annotations

import io
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")  # draw to images only; the app window is Qt's
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402

from milo_app.ui import theme  # noqa: E402
from milo_app.ui.format import pretty_name, sig  # noqa: E402

matplotlib.rcParams["mathtext.fontset"] = "cm"  # Computer Modern, LaTeX's own face

# A descriptor's symbol comes from the word in its name that names the quantity; when several words
# match, the one latest in the name wins ("PLA Mass Fraction" is a fraction, not a mass). Symbols that
# would clash get subscripts 1, 2, …; a name with none of these words is x.
SYMBOLS = {
    "temperature": "T", "pressure": "P", "volume": "V", "density": r"\rho", "fraction": r"\phi",
    "energy": "E", "time": "t", "length": "L", "mass": "m", "count": "N", "steps": "N", "atoms": "N",
    "frequency": "f", "angle": r"\theta", "alpha": r"\alpha", "beta": r"\beta", "gamma": r"\gamma",
    "force": "F", "stress": r"\sigma", "modulus": "E", "velocity": "v", "charge": "q", "seed": "s",
    "budget": "t", "iterations": "N", "units": "n", "chains": "N",
}


def tex_name(key: str) -> str:
    """"output:AmorphousCell.CellVolume" -> \\mathrm{Amorphous\\ Cell\\ Cell\\ Volume}."""
    text = pretty_name(key.partition(":")[2])
    for ch in "\\{}$&#%_^~":
        text = text.replace(ch, "\\" + ch if ch not in "\\^~" else "")
    return r"\mathrm{" + text.replace(" ", r"\ ") + "}"


def tex_number(value: float, digits: int = 4) -> str:
    """16470 -> 16{,}470 (no stray space after the comma); 5.55e-06 -> 5.55 \\times 10^{-6}."""
    text = sig(value, digits)
    if "e" in text:
        mantissa, exponent = text.split("e")
        return rf"{mantissa} \times 10^{{{int(exponent)}}}"
    return text.replace(",", "{,}")


def tex_units(units: Any) -> str:
    """"g/cm^3" -> \\mathrm{g/cm}^{3}; "A^3" -> \\mathrm{A}^{3}."""
    if not units:
        return ""
    parts = re.split(r"\^(-?\d+)", str(units))
    out = ""
    for i, part in enumerate(parts):
        if i % 2:
            out += "^{" + part + "}"
        elif part:
            out += r"\mathrm{" + part.replace(" ", r"\ ") + "}"
    return out


def symbols(fit: dict[str, Any]) -> dict[str, str]:
    """A LaTeX symbol for the target and every predictor, distinct from each other."""
    keys = [fit["target"], *fit["predictors"]]
    picked = {}
    for key in keys:
        words = pretty_name(key.partition(":")[2]).lower().replace("(", " ").split()
        hits = [(i, SYMBOLS[w]) for i, w in enumerate(words) if w in SYMBOLS]
        picked[key] = max(hits)[1] if hits else "x"
    counts: dict[str, int] = {}
    for sym in picked.values():
        counts[sym] = counts.get(sym, 0) + 1
    seen: dict[str, int] = {}
    for key in keys:
        sym = picked[key]
        if counts[sym] > 1:
            seen[sym] = seen.get(sym, 0) + 1
            picked[key] = f"{sym}_{{{seen[sym]}}}"
    return picked


def lines(fit: dict[str, Any], symbolic: bool = False) -> list[str]:
    """The formula as lines of LaTeX. With names: one line when short, one term per line when long.
    With symbols: always one line, multiplication written the way a paper would (no ×)."""
    units = tex_units(fit.get("units"))
    if symbolic:
        sym = symbols(fit)
        terms = [rf"{'-' if s < 0 else '+'} ({tex_number(abs(s))} \pm {tex_number(e, 3)})\,{sym[k]}"
                 for k, s, e in zip(fit["predictors"], fit["slopes"], fit["slope_sd"])]
        return [" ".join([f"{sym[fit['target']]} = {tex_number(fit['intercept'])}", *terms])]
    head = f"{tex_name(fit['target'])} = {tex_number(fit['intercept'])}"
    terms = []
    for key, slope, spread in zip(fit["predictors"], fit["slopes"], fit["slope_sd"]):
        sign = "-" if slope < 0 else "+"
        terms.append(rf"{sign} ({tex_number(abs(slope))} \pm {tex_number(spread, 3)}) \times {tex_name(key)}")
    tail = rf"\quad [{units}]" if units else ""
    if len(terms) <= 1:
        return [" ".join([head, *terms]) + tail]
    return [head] + [r"\qquad " + t for t in terms[:-1]] + [r"\qquad " + terms[-1] + tail]


def legend(fit: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(symbol, name, units) for every symbol in the formula, target first."""
    sym = symbols(fit)
    units = {fit["target"]: fit.get("units"), **fit.get("predictor_units", {})}
    return [(sym[k], pretty_name(k.partition(":")[2]), str(units.get(k) or ""))
            for k in [fit["target"], *fit["predictors"]]]


def source(fit: dict[str, Any], symbolic: bool = False) -> str:
    """LaTeX to paste into a document. With symbols, the equation is followed by a sentence saying
    what each symbol is, the way a paper would."""
    if symbolic:
        parts = [f"${s}$ is {n}" + (f" (${tex_units(u)}$)" if u else "") for s, n, u in legend(fit)]
        if len(parts) == 1:
            where = parts[0]
        else:
            where = ", ".join(parts[:-1]) + ("," if len(parts) > 2 else "") + " and " + parts[-1]
        return f"\\[\n{lines(fit, True)[0]}\n\\]\nwhere {where}."
    body = lines(fit)
    if len(body) == 1:
        return body[0]
    return "\\begin{aligned}\n" + " \\\\\n".join(
        ("&" + line.replace(r"\qquad ", "", 1)) if i else line.replace(" = ", " &= ", 1)
        for i, line in enumerate(body)) + "\n\\end{aligned}"


def render(fit: dict[str, Any], pixel_ratio: float = 1.0, symbolic: bool = False,
           max_width: float | None = None) -> QPixmap:
    """The formula drawn as an image, in the theme's ink, at the screen's pixel density. With
    `max_width` (screen pixels) it shrinks to fit that width."""
    text = "\n".join(f"${line}$" for line in lines(fit, symbolic))
    size = theme.SIZES["latex_symbols" if symbolic else "latex"]
    pixmap = math_image(text, size, pixel_ratio)
    if max_width and pixmap.width() / pixel_ratio > max_width:
        pixmap = math_image(text, size * max_width / (pixmap.width() / pixel_ratio) * 0.97, pixel_ratio)
    return pixmap


def math_image(text: str, size: float, pixel_ratio: float = 1.0) -> QPixmap:
    """Any mathtext ("$...$", lines split by newlines) drawn as an image."""
    figure = Figure(figsize=(0.01, 0.01))
    figure.text(0, 0, text, fontsize=size, color=theme.COLORS["ink"], linespacing=1.6)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100 * pixel_ratio, bbox_inches="tight", pad_inches=0.04,
                   transparent=True)
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    pixmap.setDevicePixelRatio(pixel_ratio)
    return pixmap
