"""Generate BIOVIA scripts that write MILO sim bundles."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from milo_app import graph as app_graph
from milo_app.config import get_settings
from milo_app.mcp import molecules as molecule_memory
from milo_app.mcp import protocols

TEMPLATES = Path(__file__).parent / "templates"
HARNESS_DS = Path(__file__).parent / "harness_ds"  # fake DiscoveryScript, so `use MdmDiscoveryScript;` resolves
WRITERS = {"python": "bundle_writer.py", "perl": "bundle_writer.pl"}

SCRIPTS: dict[str, dict[str, Any]] = {
    "ms_forcite_geomopt": {
        "template": "ms_forcite_geomopt_test.py",
        "product": "Materials Studio",
        "language": "python",
        "description": "Quick test: Forcite geometry optimization of one water molecule built in the script "
        "(Universal forcefield, Coarse quality). Runs in seconds.",
        "params": {},
    },
    "ms_pla_pcl_amorphous_cell": {
        "template": "ms_pla_pcl_amorphous_cell.py",
        "product": "Materials Studio",
        "language": "python",
        "description": "PLA/PCL blend amorphous cell: builds PLA and PCL chains (Polymer Builder), packs them at the "
        "requested PLA mass fraction (Amorphous Cell), Forcite geometry optimization, then NPT dynamics sized "
        "to fill time_budget_min. No input files needed.",
        "params": {
            "pla_mass_fraction": 0.30,
            "repeat_units": 10,
            "total_chains": 10,
            "construction_density": 1.0,
            "temperature_k": 298.0,
            "pressure_gpa": 0.0001,
            "forcefield": "COMPASSIII",
            "geomopt_max_iterations": 500,
            "time_budget_min": 5.0,
            "max_npt_steps": 200000,
            "trajectory_frames": 20,
            "random_seed": 20260911,
        },
    },
    "ds_minimization": {
        "template": "ds_minimization_test.pl",
        "product": "Discovery Studio",
        "language": "perl",
        "description": "Quick test: CHARMm Minimization protocol on ethanol built from SMILES in the script "
        "(CHARMm typing, Momany-Rone charges, Smart Minimizer, 500 steps). Runs in seconds; needs the "
        "Pipeline Pilot server connection Discovery Studio already uses.",
        "params": {},
    },
    "ds_protocol": {
        "template": "ds_protocol.pl",
        "product": "Discovery Studio",
        "language": "perl",
        "description": "Any Discovery Studio protocol, by name: optional input structure (input_molecule: a "
        "molecule saved in MILO's molecule memory, copied in exactly; or input_file: a file on the VM) "
        "typed with a forcefield and passed as structure_parameter, every requested protocol parameter applied "
        "(unknown names stop the run) and recorded as requested.<name> (the knobs MILO models), the protocol's full parameter set dumped into INPUT, then every file in "
        "the run folder and every property of every result molecule, .pK table (name value) and .csv table "
        "(one output per column) recorded as OUTPUT, labelled by where the protocol wrote it: FINAL (Output/: "
        "plain FINAL when it is the only one of its kind, else FINAL.<name>), intermediate.<path>, "
        "protocol_input.<path>. Parameters are checked against the protocol's real list once it has run on "
        "the VM (a protocol that never has is flagged unverified). "
        'parameters: {"Protocol Parameter Name": value} or {name: [value, "units"]}; look the exact names '
        'up with milo_search_docs ("<protocol> - Parameters"). forcefield "" skips typing.',
        "params": {
            "protocol_name": "Minimization",
            "parameters": {"Minimization Algorithm": "Smart Minimizer", "Minimization Max Steps": 2000},
            "input_molecule": "",
            "input_file": "",
            "structure_parameter": "Input Typed Molecule",
            "forcefield": "CHARMm",
            "partial_charges": "Momany-Rone",
            "max_download_mb": 1024,
            "title": "",
        },
    },
}
TEST_SCRIPTS = SCRIPTS  # backwards-compatible name

BUNDLE_CONTRACT = {
    "layout": {
        "<bundle_id>/INPUT/inputs.json": "every input the run used: {name: {value, units, ...}}",
        "<bundle_id>/INPUT/files/": "every input file (structures, settings dumps, the script)",
        "<bundle_id>/OUTPUT/outputs.json": "every output: {name: {value, units, ...}}",
        "<bundle_id>/OUTPUT/files/": "every output file (structures, reports, trajectories, job files)",
        "<bundle_id>/bundle.json": "bundle_id, product, module, task, status, start, finish, time_elapsed, "
        "campaign when the run is part of one, redo_of when it redoes a failed run (written LAST)",
    },
    "campaign_folder": "a run in a campaign is delivered to <drop folder>/<campaign folder>/<bundle_id>/, the "
    "campaign's name with characters Windows refuses in folder names turned into '-'. The MILO app also reads a "
    "bundle in such a folder as part of that campaign (bundle.json still has the last word).",
    "redo_of": "optional: the bundle_id of a failed run this run makes again (milo_run_errors lists them); the "
    "MILO app links the two and shows the failed one as redone.",
    "campaign": "optional: the name of a series of similar runs (the same experiment, different knob settings). "
    "Ask the user before writing a script whether the runs belong to a campaign, a new one they name or an "
    "existing one (milo_list_campaigns), and pass it to the writer; leave it out for a run that is not part of one. "
    "The MILO app groups runs by it and can work out models and formulas within one campaign.",
    "guarantees": [
        "unique random bundle_id",
        "all inputs used, reported by the script at runtime (explicit settings + full SaveSettings dump)",
        "all outputs (values + every file the job wrote)",
        "time_elapsed of the calculation in seconds",
    ],
    "helpers_on_the_bundle": [
        "bundle.apply_settings(owner, stage, [(name, value, units), ...], dump_name=None)  # applies, records each "
        "setting as INPUT, and dumps the module's FULL settings into INPUT/files/settings/",
        "bundle.record_energies(stage, doc)  # every forcefield energy term the run defined",
        "bundle.record_cell(stage, doc)  # density, volume, lengths, angles of a 3D-periodic structure",
        "bundle.record_results(stage, results, [(key, units), ...], report_name=None)  # values + the text report",
        "bundle.record_properties(stage, obj, properties, units=None, default_units=None)  # any property list",
        "bundle.safe(obj, attr)  # getattr that never raises: BIOVIA leaves properties undefined",
        "stage=None writes unprefixed names; stage='NPT' writes 'NPT.Temperature'",
        "path = bundle.molecule_file(name, MILO_MOLECULES)  # a molecule from MILO's molecule memory, checksum "
        "checked, written into INPUT/files/molecules/ and recorded; then doc = Documents.Import(path)",
    ],
    "molecules": "never type a structure into a script. Save it once (milo_save_molecule), then put a "
    "MILO_MOLECULES = {{MOLECULES}} line in the script (Perl: my %MILO_MOLECULES = {{MOLECULES}};) and "
    "milo_check_script(..., molecules=[names]) fills it in exactly. The writers check each molecule's checksum "
    "on the VM before using it.",
    "helpers_on_the_bundle_perl": [
        "$bundle->apply_parameters($protocol, $stage, [[name, value, units], ...])  # ReplaceItem each one (unknown "
        "names die), record each as INPUT, dump the protocol's FULL parameter set into INPUT/files/settings/",
        "$bundle->record_task($stage, $task)  # State, ProtocolStatus, TaskId..., the log, and EVERY file in RunPath",
        "$bundle->record_molecules($stage, $mdm_document, 'OUTPUT'|'INPUT')  # every property of every molecule",
        "$bundle->record_properties($stage, $obj, $names_or_undef, {name => units})  # any object's PropertyNames",
        "$bundle->safe($obj, 'Method', @args)  # method call that never dies",
        "my $path = $bundle->molecule_file($name, \\%MILO_MOLECULES)  # a molecule from MILO's molecule memory, "
        "checksum checked, written into INPUT/files/molecules/ and recorded; then DiscoveryScript::Open({Path => $path})",
        "MiloBundle::text($value)  # force a JSON string for a value that looks like a number",
    ],
    "checked_by": "milo_check_script runs the script against a fake BIOVIA and verifies the bundle it writes",
    "writer_usage_python": [
        "bundle = MiloBundle(MILO_BUNDLE_ID, product, module, task, MILO_DROP_DIR, title=..., campaign=MILO_CAMPAIGN)",
        "bundle.input(name, value, units) / bundle.output(name, value, units)",
        "doc.Export(bundle.input_path('x.xsd')) / bundle.output_path(...) / bundle.input_file(path) / bundle.output_file(path)",
        "snap = bundle.snapshot(); ...; bundle.capture_new_files(snap, 'OUTPUT')  # every file BIOVIA wrote",
        "with bundle.timed(): results = <BIOVIA Run call>",
        "except Exception as exc: bundle.record_error(exc)",
        "finally: bundle.finish(status)  # writes bundle.json last and copies the bundle to MILO_DROP_DIR",
    ],
    "writer_usage_perl": [
        "Discovery Studio scripts are Perl: use strict; use MdmDiscoveryScript; use ProtocolDiscoveryScript;",
        "my $MILO_BUNDLE_ID = '<uuid>'; my $MILO_DROP_DIR = '<drop dir as seen on the VM>';  # keep these two lines",
        "my $bundle = MiloBundle->new(bundle_id => $MILO_BUNDLE_ID, product => 'Discovery Studio', module => ..., "
        "task => ..., drop_dir => $MILO_DROP_DIR, work_dir => DiscoveryScript::GetTemporaryFolder(), title => ..., "
        "campaign => $MILO_CAMPAIGN);  # campaign: the name, or undef",
        "$bundle->input(name, value, units) / $bundle->output(name, value, units)",
        "$document->Save($bundle->input_path('x.dsv'), 'dsv') / $bundle->output_path(...) / $bundle->input_file($0, 'script.pl')",
        "my $task = $bundle->timed(sub { my $t = $session->Launch($protocol, $mb, True, False); $t->WaitForCompletion(); $t });",
        "eval { ...; $status = 'succeeded'; 1 } or do { $bundle->record_error($@); $status = 'failed' };",
        "$bundle->finish($status);  # writes bundle.json last and copies the bundle to MILO_DROP_DIR",
    ],
}


def bundle_writer_source(language: str = "python") -> str:
    return (TEMPLATES / WRITERS[language]).read_text(encoding="utf-8")


def perl_executable() -> str | None:
    """Perl for checking Discovery Studio scripts: MILO_PERL, else PATH, else the one Git for Windows ships."""
    for candidate in (os.environ.get("MILO_PERL"), shutil.which("perl"), r"C:\Program Files\Git\usr\bin\perl.exe"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def perl_command(*args: str) -> list[str]:
    perl = perl_executable()
    if perl is None:
        raise FileNotFoundError("no Perl found to check Discovery Studio scripts (install Git for Windows or set MILO_PERL)")
    return [perl, "-I" + str(HARNESS_DS), "-MDiscoveryScript", *args]


def perl_string(text: str) -> str:
    """A Perl single-quoted string literal: only backslash and quote need escaping (Windows paths stay intact)."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def perl_literal(value: Any) -> str:
    if value is None:
        return "undef"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(perl_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(perl_string(k) + " => " + perl_literal(v) for k, v in value.items()) + "}"
    return perl_string(value)


def check_perl_syntax(script: str) -> dict[str, Any]:
    """perl -c against the fake DiscoveryScript modules (catches Perl errors, not wrong DS method names)."""
    try:
        command = perl_command("-c")
    except FileNotFoundError as exc:
        return {"ok": None, "note": str(exc)}
    with tempfile.TemporaryDirectory(prefix="milo_perl_") as tmp:
        (Path(tmp) / "milo_script.pl").write_text(script, encoding="utf-8")
        run = subprocess.run([*command, "milo_script.pl"], cwd=tmp, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
    if run.returncode == 0:
        return {"ok": True}
    lines = [line for line in run.stderr.strip().splitlines() if "had compilation errors" not in line]
    return {"ok": False, "error": "\n".join(lines[-10:])}


def check_syntax(script: str, language: str) -> dict[str, Any]:
    return check_perl_syntax(script) if language == "perl" else check_python_syntax(script)


def check_python_syntax(script: str) -> dict[str, Any]:
    try:
        compile(script, "<milo-script>", "exec")
        return {"ok": True}
    except SyntaxError as exc:
        return {"ok": False, "error": f"line {exc.lineno}: {exc.msg}", "text": (exc.text or "").rstrip()}


def list_scripts() -> dict[str, Any]:
    """Every script and its parameters. Every parameter is required: the values shown are suggestions to
    offer the user, never filled in silently."""
    listed = {}
    for kind, spec in SCRIPTS.items():
        entry = {k: v for k, v in spec.items() if k not in ("template", "params")}
        entry["params_all_required"] = spec["params"]
        listed[kind] = entry
    return listed


def _merge_params(kind: str, params: dict[str, Any] | None) -> dict[str, Any]:
    defaults = SCRIPTS[kind]["params"]
    params = dict(params or {})
    unknown = set(params) - set(defaults)
    if unknown:
        raise ValueError(f"Unknown parameters for {kind}: {sorted(unknown)}. Allowed: {sorted(defaults)}")
    missing = [k for k in defaults if k not in params]
    if missing:
        raise ValueError(
            f"Every input must come from the user, and these were not given for {kind}: "
            + "; ".join(f"{k} (suggested: {defaults[k]!r})" for k in missing)
            + ". Ask the user for each one. If they want a suggested value, pass it explicitly.")
    merged = dict(defaults)
    for key, value in params.items():
        default = defaults[key]
        merged[key] = type(default)(value) if isinstance(default, (int, float)) and not isinstance(default, bool) else value
    if kind == "ds_protocol":
        if not str(merged["protocol_name"]).strip():
            raise ValueError("protocol_name is required")
        if not isinstance(merged["parameters"], dict):
            raise ValueError('parameters must be {"Parameter Name": value} or {name: [value, "units"]}')
        rows = []
        for name, value in merged["parameters"].items():
            value, units = value if isinstance(value, (list, tuple)) and len(value) == 2 else (value, None)
            rows.append([name, value, units])
        merged["parameters"] = rows
        if merged["input_molecule"] and merged["input_file"]:
            raise ValueError("give input_molecule or input_file, not both")
    if kind == "ms_pla_pcl_amorphous_cell":
        if not 0 < merged["pla_mass_fraction"] < 1:
            raise ValueError("pla_mass_fraction must be between 0 and 1")
        if merged["total_chains"] < 2 or merged["repeat_units"] < 1:
            raise ValueError("need total_chains >= 2 and repeat_units >= 1")
    return merged


def campaign_choice(campaign: str | None) -> str | None:
    """The user's answer to "is this part of a campaign?": a name, or "none". Anything else means the
    question was not asked."""
    answer = (campaign or "").strip()
    if not answer:
        raise ValueError('No campaign answer: ask the user whether these runs are part of a campaign (a new one '
                         'they name, or an existing one from milo_list_campaigns), then pass the name, or "none" '
                         "if they are not")
    return None if answer.lower() == "none" else answer


def perl_dq(text: str) -> str:
    """A Perl double-quoted string that holds `text` exactly on one line (newlines as \\n)."""
    out = []
    for ch in text:
        if ch in '\\"$@':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            out.append("\\x{%x}" % ord(ch))
    return '"' + "".join(out) + '"'


def molecules_literal(entries: dict[str, dict[str, Any]], language: str) -> str:
    """MILO_MOLECULES as a Python dict or a Perl hash list, every structure character for character."""
    if language == "python":
        return repr(entries)
    rows = []
    for name, entry in entries.items():
        fields = ", ".join(f"{key} => {perl_dq(str(value))}" for key, value in entry.items() if value is not None)
        rows.append(f"    {perl_dq(name)} => {{{fields}}},\n")
    return "(\n" + "".join(rows) + ")" if rows else "()"


def fill_molecules(script: str, names: list[str], language: str, product: str) -> str:
    """Put the named molecules into a script's MILO_MOLECULES placeholder ({{MOLECULES}})."""
    entries = molecule_memory.for_script(names, product)
    if names and "{{MOLECULES}}" not in script:
        raise ValueError("the script has no MILO_MOLECULES = {{MOLECULES}} line (Perl: my %MILO_MOLECULES = "
                         "{{MOLECULES}};) for the molecules to go in")
    return script.replace("{{MOLECULES}}", molecules_literal(entries, language))


def redo_target(redo_of: str | None) -> str | None:
    """The failed run a new one redoes: it must be a run in the MILO app's graph that failed."""
    redo_of = (redo_of or "").strip()
    if not redo_of:
        return None
    with app_graph.connect() as driver:
        rows, _, _ = driver.execute_query(
            "MATCH (s:Simulation {bundle_id: $id}) RETURN s.failed AS failed, s.title AS title", id=redo_of)
    if not rows:
        raise ValueError(f"redo_of: no run {redo_of} in the MILO app's graph (milo_run_errors lists the failed ones)")
    if not rows[0]["failed"]:
        raise ValueError(f"redo_of: run {redo_of} ({rows[0]['title']}) did not fail; a redo is for a failed run")
    return redo_of


def generate_script(kind: str, params: dict[str, Any] | None = None, drop_dir_vm: str | None = None,
                    campaign: str | None = None, redo_of: str | None = None) -> dict[str, Any]:
    """campaign: the user's answer, a campaign name or "none" (campaign_choice). redo_of: the bundle_id of
    the failed run this one makes again, if it is a redo."""
    campaign = campaign_choice(campaign)
    redo_of = redo_target(redo_of)
    if kind not in SCRIPTS:
        raise ValueError(f"Unknown script '{kind}'. Available: {', '.join(SCRIPTS)}")
    spec = SCRIPTS[kind]
    merged = _merge_params(kind, params)
    settings = get_settings()
    bundle_id = str(uuid.uuid4())
    drop = drop_dir_vm or settings.drop_dir_vm
    language = spec["language"]
    product = "discovery_studio" if spec["product"] == "Discovery Studio" else "materials_studio"
    if merged.get("input_molecule"):
        found = molecule_memory.find(merged["input_molecule"])
        if found is None:
            raise ValueError(f"no molecule '{merged['input_molecule']}' in MILO's molecule memory: save it first "
                             "(milo_save_molecule)")
        merged["input_molecule"] = found["name"]  # its saved name: the key it has in MILO_MOLECULES
    wanted = [merged["input_molecule"]] if merged.get("input_molecule") else []
    warnings: list[str] = []
    if kind == "ds_protocol":  # checked against the protocol's real parameter list, once it has run on the VM
        structure = merged["structure_parameter"] if merged["input_molecule"] or merged["input_file"] else None
        warnings = protocols.validate(merged["protocol_name"], merged["parameters"], structure)

    if language == "perl":
        # Perl single-quoted strings turn "\\" into "\": escape the drop path so UNC paths survive.
        drop_text = perl_string(drop)[1:-1]
        params_text = "(\n" + "".join(f"    {perl_string(k)} => {perl_literal(v)},\n" for k, v in merged.items()) + ")"
        title = merged.get("title") or merged.get("protocol_name") or kind
        run_step = ("In Discovery Studio on the VM (connected to its Pipeline Pilot server): save the script as a .pl "
                    "file, open it in the Script Window and click Run (or drag the .pl into Discovery Studio).")
        stage_note = "the work folder (DiscoveryScript::GetTemporaryFolder)"
    else:
        drop_text, params_text, title = drop, repr(merged), kind
        run_step = "In Materials Studio on the VM: File > New > Python script, paste the whole script, and Run it on the Server."
        stage_note = "the job folder"
    script = (
        (TEMPLATES / spec["template"]).read_text(encoding="utf-8")
        .replace("{{BUNDLE_WRITER}}", bundle_writer_source(language).rstrip())
        .replace("{{BUNDLE_ID}}", bundle_id)
        .replace("{{DROP_DIR}}", drop_text)
        .replace("{{PARAMS}}", params_text)
        .replace("{{TITLE}}", str(title).replace("\n", " "))
        .replace("{{CAMPAIGN}}", perl_literal(campaign) if language == "perl" else repr(campaign))
        .replace("{{REDO_OF}}", perl_literal(redo_of) if language == "perl" else repr(redo_of))
    )
    script = fill_molecules(script, wanted, language, product)
    out_dir = settings.data_dir / "generated_scripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = out_dir / f"{kind}_{bundle_id}.{'pl' if language == 'perl' else 'py'}"
    saved.write_text(script, encoding="utf-8")
    landing = drop + (f"\\{app_graph.campaign_folder(campaign)}" if campaign else "") + f"\\{bundle_id}"

    return {
        "bundle_id": bundle_id,
        "kind": kind,
        "product": spec["product"],
        "language": spec["language"],
        "params": merged,
        "campaign": campaign,
        "redo_of": redo_of,
        "molecules": [molecule_memory.summary(molecule_memory.find(n)) for n in wanted],
        "drop_dir_in_script": drop,
        "saved_copy": str(saved),
        "syntax_check": check_syntax(script, language),
        "warnings": warnings,
        "script": script,
        "next_steps": [
            run_step,
            f"The bundle is written to {landing} on the VM (a copy stays in {stage_note} as milo_bundle_{bundle_id}).",
            f"If the VM cannot write to the MILO drop folder directly, copy that folder to {settings.drop_dir}"
            + (f"\\{app_graph.campaign_folder(campaign)}" if campaign else "")
            + " on the MILO PC: the app takes it in within seconds.",
        ],
    }


def generate_test_script(kind: str = "ms_forcite_geomopt", drop_dir_vm: str | None = None) -> dict[str, Any]:
    return generate_script(kind, dict(SCRIPTS[kind]["params"]), drop_dir_vm, "none")
