"""Stand-in for Materials Studio's PyMaterialsScript, used by milo_check_script (and the tests).

It does NOT simulate anything; it only mimics the API surface MILO scripts touch, so script logic
and bundle writing can be tested without BIOVIA.
"""
import os
import sys
import time

# Materials Studio's embedded Python 3.12 is trimmed: these import fine in a normal Python but not on the VM
# (found by milo/data/generated_scripts/ms_python_env_probe.py). Hide them so tests fail like the VM would.
MISSING_ON_VM = ("json", "socket", "_socket", "zipfile")


class _VmMissingModules:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in MISSING_ON_VM:
            raise ModuleNotFoundError("No module named %r (not in Materials Studio's Python)" % name, name=name)
        return None


sys.meta_path.insert(0, _VmMissingModules())
for _name in [m for m in sys.modules if m.split(".")[0] in MISSING_ON_VM]:
    del sys.modules[_name]

MASS = {"H": 1.008, "C": 12.011, "O": 15.999}


class Point:
    def __init__(self, X=0.0, Y=0.0, Z=0.0):
        self.X, self.Y, self.Z = X, Y, Z


class _Atom:
    def __init__(self, element, point=None):
        self.ElementSymbol = element
        self.Mass = MASS.get(element, 10.0)
        self.XYZ = point


class _Atoms(list):
    @property
    def Count(self):
        return len(self)


class _Symmetry:
    Density = 1.12


class _Lattice:
    CellVolume = 21000.0
    LengthA = LengthB = LengthC = 27.6
    AngleAlpha = AngleBeta = AngleGamma = 90.0


class _TrajectoryInfo:
    NumFrames = 20


class _Doc:
    def __init__(self, name):
        self.Name = name
        self.Atoms = _Atoms()

    @property
    def ChemicalFormula(self):
        counts = {}
        for atom in self.Atoms:
            counts[atom.ElementSymbol] = counts.get(atom.ElementSymbol, 0) + 1
        return " ".join("%s%d" % kv for kv in sorted(counts.items()))

    def CreateAtom(self, element, point):
        atom = _Atom(element, point)
        self.Atoms.append(atom)
        return atom

    def CreateBond(self, a, b, kind):
        pass

    def Clean(self):
        pass

    def CreateRepeatUnit(self, head, tail, chiral=None):
        return {"doc": self, "head": head, "tail": tail, "chiral": chiral}

    def CopyFrom(self, other):
        self.Atoms = _Atoms(other.Atoms)
        if hasattr(other, "SymmetrySystem"):
            self.SymmetrySystem, self.Lattice3D = other.SymmetrySystem, other.Lattice3D

    def Export(self, path):
        with open(path, "w") as handle:
            handle.write("<XSD fake='1' name='%s' atoms='%d'/>" % (self.Name, self.Atoms.Count))


class _Documents:
    def __init__(self):
        self.docs = []

    def New(self, name):
        doc = _Doc(name)
        self.docs.append(doc)
        return doc

    def Import(self, filename):
        """Like Materials Studio: the file must exist; it becomes a document named after it."""
        if not os.path.isfile(filename):
            raise IOError("Import: no file at %s" % filename)
        return self.New(os.path.basename(filename))

    def SaveAll(self):
        for doc in self.docs:
            doc.Export(os.path.join(os.getcwd(), doc.Name))


class _Report:
    def __init__(self, text):
        self.Content = text


class _Results:
    def __init__(self, **values):
        self.__dict__.update(values)


def _write_job_file(name, text="fake"):
    path = os.path.join(os.getcwd(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


class _Configurable:
    def __init__(self):
        self.settings = {}

    def ChangeSettings(self, settings):
        self.settings.update(settings)

    def SaveSettings(self, name):
        body = "".join("<S name='%s'>%s</S>" % (k, v) for k, v in self.settings.items())
        # The real server writes settings into the job's <script>_Files/Documents/ subfolder.
        _write_job_file(os.path.join("Python%20Script_Files", "Documents", name + ".xml"), "<Settings>%s</Settings>" % body)


class _GeometryOptimization:
    def Run(self, doc, settings=None):
        doc.PotentialEnergy = -0.123
        doc.BondEnergy = 0.01
        doc.VanDerWaalsEnergy = 0.0
        _write_job_file(doc.Name.replace(".xsd", "") + " Energy.xcd")
        return _Results(Converged=True, Report=_Report("Forcite Geometry Optimization (fake report)\nConverged\n"))


class _Dynamics:
    def Run(self, doc, settings=None):
        time.sleep(0.01)
        doc.PotentialEnergy = -1500.0
        traj = _Doc(doc.Name.replace(".xsd", ".xtd"))
        traj.Trajectory = _TrajectoryInfo()
        _write_job_file(traj.Name)
        _write_job_file(doc.Name.replace(".xsd", ".trj"), "binary-ish")
        return _Results(Density=1.13, CellVolume=20950.0, Temperature=298.4, Pressure=0.0002, PotentialEnergy=-1500.0,
                        Trajectory=traj, Report=_Report("Forcite Dynamics (fake report)\n"))


class _Forcite(_Configurable):
    def __init__(self):
        super().__init__()
        self.GeometryOptimization = _GeometryOptimization()
        self.Dynamics = _Dynamics()


class _Construction:
    def __init__(self):
        self.components = []
        self.Loading = {}

    def AddComponent(self, doc):
        self.components.append(doc)

    def Run(self, settings=None):
        cell = _Doc("Construction.xtd")
        for doc in self.components:
            for _ in range(self.Loading[doc]):
                cell.Atoms.extend(doc.Atoms)
        cell.SymmetrySystem, cell.Lattice3D = _Symmetry(), _Lattice()
        _write_job_file(cell.Name)
        return _Results(Trajectory=cell, Report=_Report("Amorphous Cell Construction (fake report)\n"))


class _AmorphousCell(_Configurable):
    def __init__(self):
        super().__init__()
        self.Construction = _Construction()


class _Homopolymer:
    def Build(self, chain, unit, repeats, initiator=None, terminator=None):
        source = unit["doc"] if isinstance(unit, dict) else unit
        for _ in range(repeats):
            chain.Atoms.extend(_Atom(a.ElementSymbol) for a in source.Atoms[1:-1])
        chain.Atoms.extend([_Atom("H"), _Atom("H")])
        return chain


class _PolymerBuilder(_Configurable):
    def __init__(self):
        super().__init__()
        self.Homopolymer = _Homopolymer()


class _Modules:
    Forcite = _Forcite()
    AmorphousCell = _AmorphousCell()


class _Tools:
    PolymerBuilder = _PolymerBuilder()


Documents = _Documents()
Modules = _Modules()
Tools = _Tools()


# ---------------------------------------------------------------------------
# Permissive layer: MILO scripts can drive ANY Materials Studio module, and this
# stand-in only mimics the few used so far. Unknown modules/tools become generic
# stubs that accept settings, run, and write a job file, so a script for a module
# nobody has faked yet still exercises the MILO bundle writer end to end.
# Documents stay strict: a document only answers properties a run actually set,
# so record_energies()/record_cell() skip undefined ones exactly like the real API.
# ---------------------------------------------------------------------------
class _GenericModule(_Configurable):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.Loading = {}

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        child = _GenericModule("%s.%s" % (self.name, attr))
        setattr(self, attr, child)
        return child

    def __call__(self, *args, **kwargs):
        return self

    def __setitem__(self, key, value):
        self.Loading[key] = value

    def __getitem__(self, key):
        return self.Loading.get(key)

    def AddComponent(self, doc):
        self.Loading.setdefault(doc, 1)

    def Run(self, *args, **kwargs):
        label = self.name.replace(".", "_")
        _write_job_file("%s_fake_run.txt" % label, "fake run of %s" % self.name)
        doc = args[0] if args and isinstance(args[0], _Doc) else None
        trajectory = doc or _Doc("%s.xtd" % label)
        if doc is None:
            _write_job_file(trajectory.Name)
        return _Results(Converged=True, Trajectory=trajectory,
                        Report=_Report("%s (fake report)\n" % self.name))


def _generic(name):
    return _GenericModule(name)


_Modules.__getattr__ = lambda self, attr: _generic("Modules." + attr) if not attr.startswith("_") else None
_Tools.__getattr__ = lambda self, attr: _generic("Tools." + attr) if not attr.startswith("_") else None


def __getattr__(name):  # PEP 562: any top-level name this stand-in doesn't define
    if name.startswith("_"):
        raise AttributeError(name)
    return _generic(name)
