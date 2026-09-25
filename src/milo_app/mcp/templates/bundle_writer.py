# ===================== MILO bundle writer (contract 0.1) — do not edit =====================
# Builds a MILO sim bundle with INPUT and OUTPUT fully separated:
#   <bundle_id>/INPUT/inputs.json + INPUT/files/   <bundle_id>/OUTPUT/outputs.json + OUTPUT/files/
#   <bundle_id>/bundle.json  (written LAST; bundle_id, product, module, task, status, start, finish, time_elapsed)
# The bundle is staged in the job folder, then copied to MILO_DROP_DIR (bundle.json copied last).
# Standard library only, and only modules Materials Studio's trimmed Python ships (it has no json, socket, zipfile).
import datetime as _milo_dt
import math as _milo_math
import os as _milo_os
import shutil as _milo_shutil
import sys as _milo_sys
import time as _milo_time
import traceback as _milo_traceback

MILO_CONTRACT = "0.1"


def _milo_now():
    return _milo_dt.datetime.now(_milo_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _milo_jsonable(value):
    """Make any value JSON-safe without losing it: unknown objects become their string form."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if _milo_math.isfinite(value) else str(value)
    if isinstance(value, (list, tuple)):
        return [_milo_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _milo_jsonable(v) for k, v in value.items()}
    # BIOVIA returns some results as its own numeric/boolean wrapper types: keep them as real numbers/booleans.
    text = str(value)
    if text in ("True", "False"):
        return text == "True"
    try:
        number = float(value)
        if _milo_math.isfinite(number):
            return int(number) if number.is_integer() and text.lstrip("-").isdigit() else number
    except (TypeError, ValueError):
        pass
    try:
        return _milo_jsonable(list(value)) if hasattr(value, "__iter__") and not hasattr(value, "items") else str(value)
    except Exception:
        return str(value)


_MILO_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _milo_json_string(text):
    out = []
    for ch in text:
        code = ord(ch)
        if ch in _MILO_ESCAPES:
            out.append(_MILO_ESCAPES[ch])
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code > 0xFFFF:  # surrogate pair, same as json.dumps(ensure_ascii=True)
            code -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 | (code >> 10), 0xDC00 | (code & 0x3FF)))
        else:
            out.append("\\u%04x" % code)
    return '"' + "".join(out) + '"'


def _milo_json_text(value, level=0):
    """json.dumps(value, indent=2) without the json module (absent from Materials Studio's Python)."""
    value = _milo_jsonable(value)
    pad, inner = "  " * level, "  " * (level + 1)
    if value is None:
        return "null"
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _milo_json_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[\n" + ",\n".join(inner + _milo_json_text(v, level + 1) for v in value) + "\n" + pad + "]"
    if not value:
        return "{}"
    return "{\n" + ",\n".join(inner + _milo_json_string(k) + ": " + _milo_json_text(v, level + 1)
                              for k, v in value.items()) + "\n" + pad + "}"


class MiloBundle:
    def __init__(self, bundle_id, product, module, task, drop_dir, work_dir=None, title=None):
        self.bundle_id = bundle_id
        self.drop_dir = drop_dir
        self.work_dir = _milo_os.path.abspath(work_dir or _milo_os.getcwd())
        self.root = _milo_os.path.join(self.work_dir, "milo_bundle_" + bundle_id)
        self.inputs = {}
        self.outputs = {}
        self._input_sources = set()  # job files already filed under INPUT; never re-filed as OUTPUT
        self.time_elapsed = None
        self.manifest = {
            "bundle_id": bundle_id,
            "title": title or task,
            "product": product,
            "module": module,
            "task": task,
            "status": "running",
            "start": _milo_now(),
            "finish": None,
            "time_elapsed": None,
            "time_elapsed_units": "s",
            "milo_contract": MILO_CONTRACT,
        }
        for sector in ("INPUT", "OUTPUT"):
            _milo_os.makedirs(_milo_os.path.join(self.root, sector, "files"), exist_ok=True)

    # ---- values ----
    def input(self, name, value, units=None, **meta):
        self.inputs[name] = dict({"value": _milo_jsonable(value), "units": units}, **_milo_jsonable(meta))

    def output(self, name, value, units=None, **meta):
        self.outputs[name] = dict({"value": _milo_jsonable(value), "units": units}, **_milo_jsonable(meta))

    # ---- files ----
    def input_path(self, name):
        """Path inside INPUT/files/ for BIOVIA to write a file directly (e.g. doc.Export)."""
        return _milo_os.path.join(self.root, "INPUT", "files", name)

    def output_path(self, name):
        return _milo_os.path.join(self.root, "OUTPUT", "files", name)

    def _copy_in(self, sector, path, name=None):
        dest = _milo_os.path.join(self.root, sector, "files", name or _milo_os.path.basename(path))
        _milo_os.makedirs(_milo_os.path.dirname(dest), exist_ok=True)
        _milo_shutil.copy2(path, dest)
        return dest

    def input_file(self, path, name=None):
        return self._copy_in("INPUT", path, name)

    def output_file(self, path, name=None):
        return self._copy_in("OUTPUT", path, name)

    def write_text(self, sector, name, text):
        path = _milo_os.path.join(self.root, sector, "files", name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    # ---- job-folder sweeps: catch every file BIOVIA writes, even ones we don't know about ----
    def snapshot(self):
        state = {}
        for folder, dirs, files in _milo_os.walk(self.work_dir):
            if _milo_os.path.abspath(folder).startswith(self.root):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not _milo_os.path.abspath(_milo_os.path.join(folder, d)).startswith(self.root)]
            for name in files:
                path = _milo_os.path.join(folder, name)
                try:
                    stat = _milo_os.stat(path)
                    state[path] = (stat.st_size, stat.st_mtime)
                except OSError:
                    pass
        return state

    def capture_new_files(self, before, sector, subfolder="job", flat=False):
        """Copy every file created or changed in the job folder since `before` into <sector>/files/<subfolder>/.
        flat=True drops the job-folder subpath (e.g. "Python%20Script_Files/Documents/"), keeping paths short:
        Materials Studio's server can't handle paths over 260 characters."""
        copied = []
        for path, sig in sorted(self.snapshot().items()):
            if before.get(path) == sig:
                continue
            if sector == "OUTPUT" and path in self._input_sources:
                continue
            rel = _milo_os.path.relpath(path, self.work_dir)
            dest = _milo_os.path.join(subfolder, _milo_os.path.basename(rel) if flat else rel)
            try:
                self._copy_in(sector, path, dest)
                copied.append(rel)
                if sector == "INPUT":
                    self._input_sources.add(path)
            except OSError as exc:
                full_dest = _milo_os.path.join(self.root, sector, "files", dest)
                print("MILO: could not copy %s (%d chars) to %s (%d chars): %s"
                      % (path, len(path), full_dest, len(full_dest), exc))
        return copied

    # ---- timing ----
    class _Timer:
        def __init__(self, bundle):
            self.bundle = bundle

        def __enter__(self):
            self.t0 = _milo_time.perf_counter()
            return self

        def __exit__(self, *exc):
            self.bundle.time_elapsed = _milo_time.perf_counter() - self.t0
            return False

    def timed(self):
        """with bundle.timed(): <the BIOVIA calculation>  -> sets time_elapsed (seconds)."""
        return MiloBundle._Timer(self)

    # ---- BIOVIA helpers: same for every product, module and task ----
    ENERGY_PROPERTIES = (
        "PotentialEnergy", "ValenceDiagonalEnergy", "BondEnergy", "AngleEnergy", "TorsionEnergy", "InversionEnergy",
        "ValenceCrossTermEnergy", "StretchStretchEnergy", "SeparatedStretchStretchEnergy", "StretchBendStretchEnergy",
        "StretchTorsionStretchEnergy", "BendBendEnergy", "BendTorsionBendEnergy", "TorsionBendBendEnergy",
        "TorsionStretchEnergy", "UreyBradleyEnergy", "NonBondEnergy", "VanDerWaalsEnergy", "ElectrostaticEnergy",
        "HydrogenBondEnergy", "ThreeBodyNonBondEnergy", "RestraintEnergy", "Temperature",
    )
    CELL_PROPERTIES = (
        ("Density", "SymmetrySystem", "g/cm^3"), ("CellVolume", "Lattice3D", "A^3"),
        ("LengthA", "Lattice3D", "A"), ("LengthB", "Lattice3D", "A"), ("LengthC", "Lattice3D", "A"),
        ("AngleAlpha", "Lattice3D", "deg"), ("AngleBeta", "Lattice3D", "deg"), ("AngleGamma", "Lattice3D", "deg"),
    )

    @staticmethod
    def safe(obj, attr):
        """getattr that never raises: BIOVIA leaves many properties undefined depending on the structure."""
        try:
            return getattr(obj, attr)
        except Exception:
            return None

    def apply_settings(self, owner, stage, settings, dump_name=None):
        """Apply settings to a module/tool, record each one as an INPUT, and dump the FULL settings into INPUT.

        settings: [(name, value, units), ...] in the order to apply (Quality first: it resets other settings).
        stage: prefixes the input names when one script runs several stages (e.g. "NPT.Temperature")."""
        owner.ChangeSettings(dict((name, value) for name, value, _ in settings))
        prefix = (stage + ".") if stage else ""
        for name, value, units in settings:
            self.input(prefix + name, value, units)
        before = self.snapshot()
        owner.SaveSettings(dump_name or ("milo_settings_" + (stage or "run")))
        folder = "settings/" + stage if stage else "settings"
        self.input(prefix + "settings_dump", self.capture_new_files(before, "INPUT", folder, flat=True))

    def record_properties(self, stage, obj, properties, units=None, default_units=None):
        """Record every property BIOVIA defines on obj; undefined ones are skipped, never fatal."""
        units = units or {}
        for prop in properties:
            value = self.safe(obj, prop)
            if value is not None:
                self.output("%s.%s" % (stage, prop) if stage else prop, value, units.get(prop, default_units))

    def record_energies(self, stage, doc):
        """Every forcefield energy term the run defined on the document."""
        self.record_properties(stage, doc, self.ENERGY_PROPERTIES, {"Temperature": "K"}, "kcal/mol")

    def record_cell(self, stage, doc):
        """Density, volume, cell lengths and angles of a 3D-periodic structure."""
        for label, holder, units in self.CELL_PROPERTIES:
            source = self.safe(doc, holder)
            value = self.safe(source, label) if source is not None else None
            if value is not None:
                self.output("%s.%s" % (stage, label) if stage else label, value, units)

    def record_results(self, stage, results, keys=(), report_name=None):
        """Values off a BIOVIA results object, plus its text report as an OUTPUT file."""
        for key, units in keys:
            value = self.safe(results, key)
            if value is not None:
                self.output("%s.%s" % (stage, key) if stage else key, value, units)
        report = self.safe(results, "Report")
        content = self.safe(report, "Content") if report is not None else None
        if content:
            self.write_text("OUTPUT", "%s_report.txt" % (report_name or stage or "run"), content)

    # ---- errors ----
    def record_error(self, exc):
        self.output("error", "%s: %s" % (type(exc).__name__, exc))
        self.output("traceback", _milo_traceback.format_exc())

    # ---- finish ----
    def finish(self, status):
        self.manifest["status"] = status
        self.manifest["finish"] = _milo_now()
        self.manifest["time_elapsed"] = self.time_elapsed
        self._dump(_milo_os.path.join(self.root, "INPUT", "inputs.json"), self.inputs)
        self._dump(_milo_os.path.join(self.root, "OUTPUT", "outputs.json"), self.outputs)
        self._dump(_milo_os.path.join(self.root, "bundle.json"), self.manifest)  # last
        print("MILO: bundle %s staged at %s (status: %s)" % (self.bundle_id, self.root, status))
        self._deliver()
        return self.root

    def _dump(self, path, data):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_milo_json_text(data))

    def _deliver(self):
        if not self.drop_dir:
            print("MILO: no drop folder set; bundle stays in the job folder.")
            return
        dest = _milo_os.path.join(self.drop_dir, self.bundle_id)
        try:
            _milo_shutil.copytree(self.root, dest, ignore=_milo_shutil.ignore_patterns("bundle.json"), dirs_exist_ok=True)
            _milo_shutil.copy2(_milo_os.path.join(self.root, "bundle.json"), _milo_os.path.join(dest, "bundle.json"))
            print("MILO: bundle delivered to %s" % dest)
        except Exception as exc:
            print("MILO: could NOT copy bundle to drop folder %s: %s" % (self.drop_dir, exc))
            print("MILO: the bundle is still in the job folder; copy it to the drop folder by hand.")
# =================================== end MILO bundle writer ===================================
