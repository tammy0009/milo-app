"""Run a candidate BIOVIA script against a fake BIOVIA and check the MILO bundle it writes.

This is what makes "any simulation, same bundle" a guarantee instead of a hope: the script runs
start to finish offline, and the bundle it produces is read back with MILO's own reader.

What it proves: the script runs, its bundle is complete and well formed, and the graph can ingest it.
What it cannot prove: that the BIOVIA calls are correct. The fake accepts modules it has never
seen, and the VM is the only real check on the chemistry (use milo_search_docs for the API).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from milo_app import graph as app_graph
from milo_app.blind import bundle_values
from milo_app.bundle import find_bundles, read_bundle
from milo_app.config import get_settings
from milo_app.mcp import molecules as molecule_memory
from milo_app.mcp.scripts import (
    campaign_choice, check_perl_syntax, check_python_syntax, fill_molecules, perl_command, perl_string,
)

HARNESS = Path(__file__).parent / "harness"  # fake Materials Studio (Python)
# Discovery Studio (Perl): my $MILO_DROP_DIR = '...';  (single-quoted, with \\ and \' escaped)
DROP_LINE_PERL = re.compile(r"^(\s*my\s+)?\$MILO_DROP_DIR\s*=\s*'(?:[^'\\]|\\.)*'\s*;[ \t]*$", re.MULTILINE)
SCRIPT_NAMES = ("script.py", "script.pl")
DROP_LINE = re.compile(r'^MILO_DROP_DIR\s*=\s*r?(["\']).*?\1[ \t]*$', re.MULTILINE)
TAIL_LINES = 40


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    kept = text.strip().splitlines()[-lines:]
    return "\n".join(kept)


def _contract_problems(bundle, delivered: bool) -> list[str]:
    """Checks on top of read_bundle's: every MILO guarantee, checked on a real bundle."""
    problems: list[str] = []
    manifest = bundle.manifest if isinstance(bundle.manifest, dict) else {}
    status = manifest.get("status")
    if status in (None, "running"):
        problems.append("bundle.json status is %r: finish(status) never ran (call it in a finally block)" % status)
    elapsed = manifest.get("time_elapsed")
    if not isinstance(elapsed, (int, float)):
        problems.append("time_elapsed is %r: wrap the calculation in `with bundle.timed():`" % elapsed)
    if not bundle.items_in("INPUT"):
        problems.append("no INPUT values: record every setting the run used with bundle.input()/apply_settings()")
    if not bundle.items_in("OUTPUT"):
        problems.append("no OUTPUT values: record what the run produced with bundle.output()/record_*()")
    if not any(f.relpath.endswith(SCRIPT_NAMES) for f in bundle.files_in("INPUT")):
        problems.append("the script itself is not in INPUT/files: keep the bundle.input_file(__file__) block "
                        "(Perl: $bundle->input_file($0, 'script.pl'))")
    stray = [f.relpath for f in bundle.files_in("OUTPUT") if "settings" in f.relpath.lower()]
    if stray:
        problems.append("settings files under OUTPUT (INPUT and OUTPUT must stay separate): %s" % ", ".join(stray[:3]))
    if not delivered:
        problems.append("bundle never reached the drop folder: check the MILO_DROP_DIR delivery at the end of finish()")
    return problems


def check_script(script: str, timeout_s: int = 180, product: str = "materials_studio",
                 campaign: str | None = None, molecules: list[str] | None = None) -> dict[str, Any]:
    """Run `script` against the fake BIOVIA in a temp job folder and report on the bundle it wrote.

    product: materials_studio (Python, fake PyMaterialsScript) or discovery_studio (Perl, fake DiscoveryScript).
    campaign: the user's answer (a name or "none"); the bundle must carry exactly that.
    molecules: names from MILO's molecule memory to put into the script's {{MOLECULES}} placeholder; each must
    then be used through molecule_file. The finished script (molecules in) is saved and returned."""
    perl = product.lower().startswith("discovery")
    campaign = campaign_choice(campaign)
    molecules = list(molecules or [])
    unknown = [n for n in molecules if molecule_memory.find(n) is None]
    if unknown:
        raise ValueError("not in MILO's molecule memory: %s (save them first with milo_save_molecule)" % ", ".join(unknown))
    molecules = [molecule_memory.find(n)["name"] for n in molecules]  # saved names: the keys in MILO_MOLECULES
    filled = fill_molecules(script, molecules, "perl" if perl else "python",
                            "discovery_studio" if perl else "materials_studio")
    final = {}
    if filled != script:
        script = filled
        final = {"script": script, "note_script": "the molecules are now in the script: give the user this script "
                                                  "(or the saved copy), never a retyped one"}
    syntax = check_perl_syntax(script) if perl else check_python_syntax(script)
    if syntax["ok"] is None:  # no Perl on this PC
        return {"ok": None, "stage": "syntax", "syntax": syntax, "problems": [syntax["note"]]}
    if not syntax["ok"]:
        return {"ok": False, "stage": "syntax", "syntax": syntax, "problems": [syntax["error"]]}

    with tempfile.TemporaryDirectory(prefix="milo_check_") as tmp:
        job, drop = Path(tmp) / "job", Path(tmp) / "drop"
        job.mkdir()
        drop.mkdir()
        # Never deliver a check run into the real drop folder.
        if perl:
            patched, replaced = DROP_LINE_PERL.subn(
                lambda m: "%s$MILO_DROP_DIR = %s;" % (m.group(1) or "", perl_string(drop)), script, count=1)
            script_path = job / "milo_candidate_script.pl"
            command = perl_command(script_path.name)
            env = dict(os.environ, COMPUTERNAME="MILO-CHECK")
        else:
            patched, replaced = DROP_LINE.subn(lambda _m: 'MILO_DROP_DIR = r"%s"' % drop, script, count=1)
            script_path = job / "milo_candidate_script.py"
            command = [sys.executable, script_path.name]
            env = dict(os.environ, PYTHONPATH=str(HARNESS), COMPUTERNAME="MILO-CHECK")
        script_path.write_text(patched, encoding="utf-8")

        try:
            run = subprocess.run(
                command, cwd=job, timeout=timeout_s,
                capture_output=True, text=True, stdin=subprocess.DEVNULL,  # never read the MCP client's stream
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "problems": ["script did not finish within %ds" % timeout_s]}

        result: dict[str, Any] = {
            "stage": "run",
            "exit_code": run.returncode,
            "stdout_tail": _tail(run.stdout),
            "harness": "fake %s: proves the bundle, not the BIOVIA calls"
                       % ("DiscoveryScript (Perl)" if perl else "PyMaterialsScript (Python)"),
        }
        if not replaced:
            result["note_drop_dir"] = "no MILO_DROP_DIR line found; delivery to the drop folder was not exercised"
        if run.returncode != 0:
            result.update(ok=False, problems=["script crashed before writing a bundle"], stderr_tail=_tail(run.stderr))
            return result

        delivered = find_bundles(drop)
        staged = find_bundles(job)
        roots = delivered or staged
        if not roots:
            result.update(ok=False, problems=["no bundle was written (expected MILO_DROP_DIR/<bundle_id>/bundle.json)"])
            return result

        bundle = read_bundle(roots[0])
        problems = list(bundle.problems) + _contract_problems(bundle, bool(delivered))

        # The point of a bundle is to become a simulation in the MILO app: prove it with the app's own
        # reader, the one that makes its descriptors.
        graph: dict[str, Any] = {}
        try:
            values = bundle_values(bundle)
            knobs = sorted(k for k in values if k.startswith("input:requested."))
            graph = {"descriptors": len(values), "knobs": knobs, "title": app_graph.title_of(bundle),
                     "sample": sorted(values)[:8]}
            if not any(k.startswith("output:") for k in values):
                problems.append("no output becomes a descriptor in the MILO app")
            if not knobs:
                problems.append("no requested.* inputs: the MILO app has no knobs to model or make ghosts from")
        except Exception as exc:
            problems.append("the MILO app cannot read this bundle: %s: %s" % (type(exc).__name__, exc))
        # The user's answers must be what the run records.
        recorded = app_graph.campaign_of(bundle.manifest)
        if recorded != campaign:
            problems.append("the bundle's campaign is %r but the user's answer was %r: pass campaign=MILO_CAMPAIGN to "
                            "the writer, with MILO_CAMPAIGN set to that answer" % (recorded, campaign or "none"))
        if delivered:
            where = Path(delivered[0]).resolve().relative_to(drop.resolve()).parts
            want = (app_graph.campaign_folder(campaign), bundle.bundle_id) if campaign else (bundle.bundle_id,)
            if where != want:
                problems.append("delivered to %s in the drop folder, not %s: a run in a campaign goes in its "
                                "campaign's folder (the bundle writer does this from campaign=...)"
                                % ("/".join(where), "/".join(want)))
        inputs = {item.name for item in bundle.items_in("INPUT")}
        for name in molecules:
            if not any(n.startswith("molecule.") and n.endswith(".checksum") and n[9:-9].lower() == name.lower()
                       for n in inputs):
                problems.append("molecule %r is in the script but never used: load it with molecule_file(%r, "
                                "MILO_MOLECULES), never with a typed-in structure" % (name, name))
        outputs = {item.name: item.value for item in bundle.items_in("OUTPUT")}
        saved = get_settings().data_dir / "generated_scripts" / (
            "checked_%s.%s" % (bundle.bundle_id, "pl" if perl else "py"))
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(script, encoding="utf-8")
        result.update(
            saved_copy=str(saved),
            campaign=campaign,
            **final,
            ok=not problems and bundle.manifest.get("status") != "failed",
            bundle_id=bundle.bundle_id,
            manifest=bundle.manifest,
            problems=problems,
            counts={
                "inputs": len(bundle.items_in("INPUT")),
                "outputs": len(bundle.items_in("OUTPUT")),
                "input_files": len(bundle.files_in("INPUT")),
                "output_files": len(bundle.files_in("OUTPUT")),
            },
            input_names=[item.name for item in bundle.items_in("INPUT")],
            output_names=[item.name for item in bundle.items_in("OUTPUT")],
            files=[f.relpath for f in bundle.files_in("INPUT")] + [f.relpath for f in bundle.files_in("OUTPUT")],
            graph=graph,
        )
        if "error" in outputs:  # the script caught its own failure and recorded it, as the contract requires
            result["run_error"] = outputs["error"]
            result["run_traceback"] = _tail(str(outputs.get("traceback", "")), 20)
        return result
