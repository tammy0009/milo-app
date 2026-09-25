"""How names, numbers, units and formulas read on screen and in LaTeX."""
from __future__ import annotations

from milo_app.ui import latex
from milo_app.ui.format import pretty_name, short_title, sig, units_text


def test_pretty_names():
    assert pretty_name("requested.pla_mass_fraction") == "Requested PLA Mass Fraction"
    assert pretty_name("requested.temperature_k") == "Requested Temperature (K)"
    assert pretty_name("GeometryOptimization.CellVolume") == "Geometry Optimization Cell Volume"
    assert pretty_name("VdWSummationMethod") == "vdW Summation Method"


def test_short_titles():
    assert short_title("PLA30/PCL70 amorphous cell") == "PLA30/PCL70…"
    assert short_title("Geometry: a long task") == "Geometry"


def test_numbers_and_units():
    assert sig(16470.2) == "16,470"
    assert sig(0.0342) == "0.0342"
    assert sig(-2795.3) == "-2,795"
    assert units_text("g/cm^3") == "g/cm³"
    assert units_text("A^-1") == "A⁻¹"


def fit(target: str, predictors: list[str]) -> dict:
    return {"target": target, "predictors": predictors, "intercept": 15595.2, "slopes": [-2620.0, 2.3][:len(predictors)],
            "slope_sd": [2521.0, 6.63][:len(predictors)], "units": "A^3",
            "predictor_units": {p: None for p in predictors}}


def test_latex_with_names_and_with_symbols():
    two = fit("output:AmorphousCell.CellVolume", ["input:requested.pla_mass_fraction", "input:requested.temperature_k"])
    names = latex.source(two)
    assert names.startswith("\\begin{aligned}") and r"15{,}595" in names and r"\pm 2{,}521" in names
    symbols = latex.source(two, symbolic=True)
    assert r"V = 15{,}595 - (2{,}620 \pm 2{,}521)\,\phi + (2.3 \pm 6.63)\,T" in symbols
    assert "where $V$ is Amorphous Cell Cell Volume" in symbols


def test_clashing_symbols_get_subscripts():
    sym = latex.symbols(fit("output:NPT.Density", ["output:AmorphousCell.Density"]))
    assert sorted(sym.values()) == [r"\rho_{1}", r"\rho_{2}"]
