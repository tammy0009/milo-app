"""MILO MCP (local, stdio): BIOVIA docs + scripts that write MILO sim bundles, and the MILO app's graph."""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from milo_app.mcp import check, docs, graph_tools, molecules, scripts

mcp = MCPServer(
    "milo",
    instructions=(
        "MILO writes BIOVIA (Materials Studio / Discovery Studio) scripts whose output is a MILO sim bundle: "
        "INPUT and OUTPUT fully separated, a random bundle_id, every input used, every output, and time elapsed. "
        "Materials Studio scripts are Python; Discovery Studio scripts are Perl (protocols launched through "
        "ProtocolDiscoveryScript). Search the BIOVIA docs before writing custom script code. Every script must use "
        "the MILO bundle writer for its language (see milo_bundle_contract), and pass milo_check_script before "
        "it goes to the user. "
        "Three rules for every script, no exceptions: "
        "(1) CAMPAIGN: ask the user whether these runs are part of a campaign (a named series of similar runs: the "
        "same experiment with different knob settings), a new one they name or an existing one from "
        "milo_list_campaigns, and pass their answer (the name, or \"none\"). "
        "(2) EVERY INPUT FROM THE USER: every setting the run uses must be one the user gave or explicitly accepted. "
        "List them all (milo_list_scripts shows each script's parameters, with suggested values), ask for each, "
        "and never fill one in silently; for a Discovery Studio protocol, look its full parameter list up with "
        "milo_search_docs and confirm every value. "
        "(3) MOLECULES FROM MEMORY: never type a structure (SMILES, coordinates, atom lists) into a script. Look the "
        "molecule up (milo_list_molecules); if it is new, get its SMILES or structure file from the user, save it "
        "with milo_save_molecule, and read its formula, weight and SMILES back to the user to confirm. Scripts get "
        "molecules from MILO itself: input_molecule for ds_protocol, or for a custom script a MILO_MOLECULES = "
        "{{MOLECULES}} line (Perl: my %MILO_MOLECULES = {{MOLECULES}};), bundle.molecule_file(name, MILO_MOLECULES) "
        "to load each one, and milo_check_script(..., molecules=[names]) to fill them in; give the user the "
        "script it returns. "
        "The MILO app's graph holds every finished simulation: read it with milo_graph_data. Predictions "
        "(ghost nodes: runs not made yet) come from the app's own calculation and from you: reason from the data, "
        "and physics you can justify, and store yours with milo_add_prediction, with an honest confidence."
    ),
)


@mcp.tool()
def milo_search_docs(query: str, product: str = "all", limit: int = 8) -> list[dict[str, Any]]:
    """Search the BIOVIA documentation. product: 'materials_studio', 'discovery_studio', or 'all'."""
    return docs.search(query, product, max(1, min(limit, 25)))


@mcp.tool()
def milo_get_doc(doc_id: int, offset: int = 0, max_chars: int = 12000) -> dict[str, Any]:
    """Read one documentation record found by milo_search_docs (page with offset for long records)."""
    return docs.get_doc(doc_id, offset, max_chars) or {"error": f"No doc {doc_id}"}


@mcp.tool()
def milo_index_docs() -> dict[str, int]:
    """Rebuild the BIOVIA documentation index. Returns record counts per product."""
    return docs.build_index()


@mcp.tool()
def milo_list_scripts() -> dict[str, Any]:
    """List the BIOVIA scripts MILO can generate, with their parameters and defaults."""
    return scripts.list_scripts()


@mcp.tool()
def milo_generate_script(
    kind: str, campaign: str, params: dict[str, Any] | None = None, drop_dir_vm: str | None = None
) -> dict[str, Any]:
    """Generate a paste-ready BIOVIA script that writes a complete MILO bundle.

    campaign: ASK THE USER FIRST whether these runs are part of a campaign (a named series of similar runs:
    the same experiment with different knob settings). Offer the existing ones (milo_list_campaigns) or a new
    name they choose. Pass that name, or "none" if the runs are not part of a campaign; a blank is refused.
    Every script in one series must use exactly the same name.

    kind: one of milo_list_scripts (e.g. 'ms_pla_pcl_amorphous_cell', 'ms_forcite_geomopt', 'ds_minimization',
    'ds_protocol').
    params: EVERY parameter of that script (milo_list_scripts), each one given or explicitly accepted by the user;
    a missing one is refused. For ds_protocol, input_molecule is the name of a molecule in MILO's molecule memory
    (milo_list_molecules / milo_save_molecule); MILO copies its saved structure into the script exactly.
    drop_dir_vm: the drop folder path as seen from inside the BIOVIA VM (defaults to MILO_DROP_DIR_VM).
    Give the user the full 'script' text: Python for Materials Studio, Perl (.pl) for Discovery Studio.
    """
    return scripts.generate_script(kind, params, drop_dir_vm, campaign)


@mcp.tool()
def milo_bundle_contract(include_writer_source: bool = False, language: str = "all") -> dict[str, Any]:
    """The MILO bundle contract, how to use the bundle writer, and optionally its full source to embed in custom scripts.

    language: 'python' (Materials Studio), 'perl' (Discovery Studio) or 'all' - which writer source to include."""
    contract = dict(scripts.BUNDLE_CONTRACT)
    if include_writer_source:
        for lang in ("python", "perl"):
            if language in (lang, "all"):
                contract["writer_source_" + lang] = scripts.bundle_writer_source(lang)
    return contract


@mcp.tool()
def milo_check_script_syntax(script: str, language: str = "python") -> dict[str, Any]:
    """Check a generated or edited script for syntax errors. language: 'python' (Materials Studio) or 'perl'
    (Discovery Studio; perl -c against MILO's fake DiscoveryScript modules)."""
    language = language.lower()
    if language not in ("python", "perl"):
        return {"ok": None, "note": "language must be 'python' or 'perl'."}
    return scripts.check_syntax(script, language)


@mcp.tool()
def milo_check_script(
    script: str, campaign: str, product: str = "materials_studio", molecules: list[str] | None = None,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Run a BIOVIA script against MILO's fake BIOVIA and check the bundle it writes. Run this before
    giving any script to the user.

    campaign: the user's answer (a campaign name, or "none"); the bundle must record exactly that.
    molecules: molecules from MILO's molecule memory the script uses. The script must have a MILO_MOLECULES =
    {{MOLECULES}} line (Perl: my %MILO_MOLECULES = {{MOLECULES}};) and load each with molecule_file; MILO fills
    them in exactly, checks the script, and returns it with a saved copy. Give the user THAT script.

    Reports: every MILO guarantee (bundle_id, all inputs, all outputs, time elapsed, INPUT/OUTPUT separation),
    what the script recorded, and any problem found by MILO's own bundle reader.
    It proves the script runs and its bundle is complete; it does NOT prove the BIOVIA calls are right
    (the fake accepts modules it has never seen) - use milo_search_docs for the API and the VM for the science.
    product: 'materials_studio' (Python, fake PyMaterialsScript) or 'discovery_studio' (Perl, fake DiscoveryScript:
    any protocol name is accepted; Minimization and Calculate Energy have their real parameter lists).
    """
    return check.check_script(script, timeout_s=max(10, min(timeout_s, 600)), product=product,
                              campaign=campaign, molecules=molecules)


@mcp.tool()
def milo_graph_data(fields: list[str] | None = None) -> dict[str, Any]:
    """Read the MILO app's graph: every simulation (its bundle.json fields, inputs and outputs with units) and every
    prediction already made (by the app's calculation or by you), with its confidence.

    fields: only inputs/outputs whose name contains one of these (case-insensitive), e.g. ["density", "temperature"].
    Leave it out to get everything. Long values (settings dumps, arrays) are shortened."""
    return graph_tools.graph_data(fields)


@mcp.tool()
def milo_save_molecule(
    name: str, smiles: str = "", file_path: str = "", notes: str = "", aliases: list[str] | None = None,
    allow_undefined_stereo: bool = False, replace: bool = False,
) -> dict[str, Any]:
    """Memorize a molecule the user gives, exactly, so scripts can use it by name.

    Give exactly one of smiles (as the user wrote it; never one you made up or recalled yourself) or file_path (a
    structure file on this PC: .mol/.sdf/.pdb/.mol2 for both products, .xsd/.car/.cif/... for Materials Studio
    only). A SMILES is checked by RDKit and turned into a 3D structure with every hydrogen, which must be the same
    molecule, stereo included. A SMILES that leaves stereo open is refused: ask the user which arrangement they
    mean, or pass allow_undefined_stereo=True if they say any will do.
    Returns the formula, weight, canonical SMILES and InChIKey: read them back to the user to confirm it is the
    molecule they mean before using it. aliases: other names the user calls it. replace=True changes a saved
    molecule (only when the user asks)."""
    return molecules.save(name, smiles, file_path, notes, aliases, allow_undefined_stereo, replace)


@mcp.tool()
def milo_list_molecules() -> list[dict[str, Any]]:
    """Every molecule in MILO's molecule memory: name, other names, formula, weight, SMILES, InChIKey, format, and
    which products can read it. Check here before asking the user for a structure."""
    return molecules.list_molecules()


@mcp.tool()
def milo_get_molecule(name: str, include_structure: bool = False) -> dict[str, Any]:
    """One saved molecule by name or other name. include_structure=True adds the saved structure file's text (to
    show the user; scripts get it through MILO, never retyped)."""
    entry = molecules.find(name)
    if entry is None:
        return {"error": f"no molecule '{name}' in MILO's molecule memory"}
    return entry if include_structure else molecules.summary(entry)


@mcp.tool()
def milo_list_campaigns() -> dict[str, Any]:
    """The campaigns already in the MILO app's graph, newest first: each one's runs, when it started and last
    ran, and which requested.* settings (knobs) vary across it. Offer these when asking the user which campaign
    new runs belong to."""
    return graph_tools.campaigns()


@mcp.tool()
def milo_add_prediction(
    title: str,
    proposed_inputs: dict[str, Any],
    predicted: dict[str, Any],
    confidence: float,
    reasoning: str,
    based_on: list[str],
    model: str = "MCP reasoning",
) -> dict[str, Any]:
    """Add a prediction (a ghost node) to the MILO app's graph: a run that has not happened yet and what it should give.

    title: short, e.g. "PLA 50 % at 350 K".
    proposed_inputs: the run's settings, named like the simulations' inputs, e.g.
        {"requested.pla_mass_fraction": 0.5, "requested.temperature_k": {"value": 350, "units": "K"}}.
    predicted: the outputs you predict, e.g. {"AmorphousCell.Density": {"value": 1.12, "spread": 0.03, "units": "g/cm^3"}};
        spread is your +/- range.
    confidence: 0 to 1. Be honest: the app's own calculation scores data support x closeness to real runs, so a few
        sims give a few percent. Go higher only when your reasoning adds real knowledge (established physics, a
        clear trend across many runs, literature) and say so.
    reasoning: why these numbers and this confidence, in plain words. Required; shown in the app.
    based_on: bundle_ids of the simulations this rests on (from milo_graph_data); the ghost links to them.
    """
    return graph_tools.add_prediction(title, proposed_inputs, predicted, confidence, reasoning, based_on, model)


@mcp.tool()
def milo_remove_prediction(prediction_id: str) -> dict[str, Any]:
    """Remove one of your own predictions (made with milo_add_prediction). Calculated ones are the app's and
    follow the data."""
    return graph_tools.remove_prediction(prediction_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
