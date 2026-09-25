# MILO app

A desktop app (Qt / PySide6) over a Neo4j graph of BIOVIA sim bundles. Open it and it brings up
Docker and its database in the background, then keeps the graph in step with the drop folder:
every finished bundle that lands in `C:\milo_drop` appears within seconds.

Scripts are written by the MILO MCP (`src/milo_app/mcp/`, below). They write bundles like this:

```
<bundle_id>/
  INPUT/   inputs.json  + files/
  OUTPUT/  outputs.json + files/
  bundle.json           (written last: its presence means the run is finished)
```

`bundle.json` may also carry a `campaign`: the name of a series of similar runs (the same experiment,
different knob settings). It is optional; a run that is not part of one has no `campaign` field. The
app takes it in like any other field, so it is also a descriptor ("Campaign = PLA/PCL sweep").

A campaign's runs can also sit in a folder of their own inside the drop folder:
`C:\milo_drop\<campaign>\<bundle_id>\`. A bundle there belongs to that campaign; if its
`bundle.json` names a different campaign, `bundle.json` wins and the mismatch is noted on the run.
The MILO MCP's scripts deliver campaign runs into that folder themselves. A run made again because
an earlier one failed names it in `redo_of`.

**Failed runs** (a status other than `succeeded`, or an `error` recorded) are pointed out as soon as
they arrive:
- the status bar says which run failed and why;
- on the graph they get a red ring and a "!" badge, and the line at the top counts them;
- in Campaigns and the Sim Feed they are red and marked "failed";
- a failed run's panel opens with its error and the end of its traceback.
A failed run that a later run redid (`redo_of`) turns grey. The MCP's `milo_run_errors` lists them all
and says, per campaign, whether they failed the same way (fix the cause, rerun the campaign) or in
different ways (redo those runs).

## What you need

- **Windows 10 or 11.** The app starts Docker Desktop itself from its standard install folder.
- **Docker Desktop**: MILO's database runs in it (the `neo4j:5.26` image downloads on first start).
- **uv** (https://docs.astral.sh/uv/): installs Python 3.12+ and every library listed in `uv.lock`.
- **Git**, and access to this repo (it is private).

Only for the MILO MCP:

- **Claude Code**, to use the MCP.
- **Perl**, for checking Discovery Studio scripts. Git for Windows already includes it.
- **BIOVIA itself** (Materials Studio / Discovery Studio), to run the scripts and make bundles.

A fresh copy starts with an empty graph. It fills as bundles land in the drop folder.

## Run it

```powershell
copy .env.example .env           # once: then set the database password and folders in .env
uv sync
.\scripts\install-shortcut.ps1   # once: MILO icon on the Desktop and in the Start menu
uv run milo-app                  # or run it from a terminal
```

Checking that everything works:

```powershell
uv run pytest                        # how names, numbers and formulas are written
```

Closing the window stops the app. Docker and the database keep running because they hold the
data, and the next launch finds them already up.

## Where things are

| File | What it does |
|---|---|
| `src/milo_app/ui/theme.py` | **All of the look**: colors, fonts, sizes, and the stylesheet |
| `src/milo_app/ui/window.py` | The window: starting screen, then descriptors · graph, and the detail views |
| `src/milo_app/ui/graph_view.py` | The graph canvas: sim blocks, descriptor nodes, links, the force layout |
| `src/milo_app/ui/descriptors.py` | The descriptor sidebar: check a field to show it on the graph |
| `src/milo_app/ui/formulas.py` | The Ghosts tab: the formula between any descriptors, with chart and calculator |
| `src/milo_app/ui/latex.py` | Formulas typeset like LaTeX, and the source for Copy LaTeX |
| `src/milo_app/ui/drawer.py` | The side panel that slides over the graph when a node is tapped |
| `src/milo_app/ui/format.py` | How values and field names read on screen (`pretty_name`: its word tables teach it acronyms and units) |
| `src/milo_app/watcher.py` | Watches the drop folder and ingests new or changed bundles |
| `src/milo_app/graph.py` | The Neo4j graph model, ingestion, and queries |
| `src/milo_app/predict.py` | Calculated ghost nodes: the model, the candidates, the confidence |
| `src/milo_app/blind.py` | Blind tests: every new run predicted before it is added, then checked |
| `src/milo_app/mcp/` | The MILO MCP: script writing, docs search, script checks, graph tools |
| `src/milo_app/mcp/knowledge/` | The BIOVIA knowledge files the MCP's docs search reads |
| `src/milo_app/services.py` | Starts Docker (clearing the stale socket files that block it) and Neo4j |
| `src/milo_app/bundle.py` | Reads a bundle folder: the ingestion contract |
| `.env` | Database address and password, drop folder, scan interval |
| `compose.yaml` | The database container: `milo-app-neo4j`, its own volume |

## The graph

```
(:Simulation {bundle_id, title, every bundle.json field, source_path, ...})
    -[:HAS_INPUT]->  (:Input  {name, order, value, value_json, units})
    -[:HAS_OUTPUT]-> (:Output {name, order, value, value_json, units})
    -[:HAS_FILE]->   (:File   {sector, relpath, size, sha256})
```

**Models, relationships and ghosts** follow the rule book in [ghost.md](ghost.md): what they claim,
how every number and confidence is calculated, and how they are tested (only ever against real runs).
In short, recalculated after every new run (`src/milo_app/predict.py`):

- **Models:** every descriptor is fitted from the knobs (the `requested.*` settings) with Bayesian
  linear regression, within its family of runs; values that never change use the rule of succession.
- **Relationships:** every pair of numeric descriptors, with confidence from Efron's local false
  discovery rate.
- **Ghosts:** each real run with one knob moved to an untried value, guessing every descriptor with a
  90 % range and a confidence. `MILO_GHOSTS` in `.env` sets how many per run and knob.
- **Blind tests** (`src/milo_app/blind.py`): every new run is predicted before it is added, then
  checked; a matching ghost becomes verified. The track record is in the Ghosts tab.
- **From the MCP:** `milo_graph_data` reads the graph, `milo_add_prediction` stores a reasoned ghost
  (confidence and written reasoning required), `milo_remove_prediction` takes one back.

```
(:Relationship {id, name, a, b, ...})                 a / b are descriptor group keys, e.g. "output:Tg"
(:Prediction {id, title, model, confidence, ...}) -[:BASED_ON]-> (:Simulation)
(:Model {id, descriptor, method, ...})                how one descriptor is guessed (ghost.md 4)
```

## The main graph

Drag a node to move it. Clicking a node fades in a little arrow where you clicked, which fades away
again; double-click anywhere on the node and the side panel slides in with its details.

Four kinds of node, each switched on or off in the **Show** bar above the graph:

| Kind | Looks like | What it is |
|---|---|---|
| Simulations | black circle, short name underneath | a run that happened |
| Descriptors | white pill in its group's color | one value of a field ("module = Forcite", "Tg = 350 K"), linked to the sims that have it |
| Relationships | black diamond | between two descriptors (Tg and water uptake) |
| Predictions | dashed ghost circle | a run the ML suggests, linked to the sims it was predicted from |

Which descriptors appear is chosen with the check boxes in the left sidebar: every `bundle.json`
field, input, and output is listed there, so a new field (e.g. `campaign`) shows up the moment a
bundle has it. Each distinct value is its own node ("time_elapsed = 3.78 min"), linked to the
sims that have that value.
Under the fields, the runs, each with a check box: uncheck a run to take it and its ghosts off the
graph; new runs arrive checked.
- **Campaigns**: one dropdown per campaign, newest first (the one with the latest run on top), its
  runs inside. The campaign's own box shows or hides all of them. Runs in no campaign are under **None**.
  Click the dot next to a campaign's name to pick its color. On the graph, a slowly spinning dotted
  ring in that color surrounds the campaign's runs; drag the ring to move them all at once.
- **Sim Feed**: every run, newest first, by title and the time it landed (its finish time).
A run's box in Campaigns and in the Sim Feed is the same switch.
A relationship pulls in its two descriptors even if they are not checked (in grey). What you
check is remembered between launches.

Neo4j Browser: http://localhost:7475 (user `neo4j`, password `milo-app-password`), Bolt on 7688.
The old MILO database (`milo-neo4j`, 7474/7687) is separate and untouched.

## MILO MCP

The MCP lives here too (`src/milo_app/mcp/`): it writes BIOVIA scripts (Materials Studio Python,
Discovery Studio Perl) whose output is a MILO bundle, searches the BIOVIA docs, checks scripts before
they go to the VM, and reads and adds to this app's graph (`milo_graph_data`, `milo_add_prediction`,
`milo_remove_prediction`).

Three rules hold for every script it writes, and the tools refuse to go on without them:

- **Campaign:** it asks whether the runs are part of a campaign, a new one you name or an existing
  one (`milo_list_campaigns`). The answer is a name or "none"; a blank is refused, and the check
  confirms the bundle records exactly that answer.
- **Every input from you:** every setting of a script must be given explicitly. The suggested values
  in `milo_list_scripts` are only offered to you, never filled in silently, and a missing one is refused.
- **Molecules from memory:** a molecule is given once, as a SMILES or a structure file, and saved
  with `milo_save_molecule`. RDKit checks it and returns its formula, weight, SMILES and InChIKey for
  you to confirm. A SMILES that leaves stereo open is refused until you choose. Scripts then get the
  saved structure from MILO, character for character (a SMILES becomes a 3D MOL file with every
  hydrogen, checked to be the same molecule). It carries a checksum that the script checks on the VM
  before using it. The memory is in `data/molecules/`, one file per molecule.

It is a local stdio server; Claude Code runs it with:

```powershell
claude mcp add milo -s user -- uv --directory C:\path\to\milo-app run milo-mcp
```

Its BIOVIA knowledge files ship with it in `src/milo_app/mcp/knowledge/` (Materials Studio and
Discovery Studio). The search index over them, `data/docs.sqlite`, is built on the first search
(`milo_index_docs` rebuilds it after the files change). The index and the scripts it writes
(`data/generated_scripts/`) stay in `data/`. `milo_check_script` runs a script against a stand-in for BIOVIA in a temporary folder that is deleted afterwards: it checks the script's structure
and bundle, and nothing it produces ever reaches the drop folder or the graph.
