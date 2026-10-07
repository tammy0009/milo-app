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

MASS = {"H": 1.008, "C": 12.011, "N": 14.007, "O": 15.999, "S": 32.06, "K": 39.098, "Ca": 40.078}


class Point:
    def __init__(self, X=0.0, Y=0.0, Z=0.0):
        self.X, self.Y, self.Z = X, Y, Z


class _Atom:
    def __init__(self, element, point=None, molecule=0):
        self.ElementSymbol = element
        self.Mass = MASS.get(element, 10.0)
        self.XYZ = point
        self.molecule = molecule  # which molecule (connected fragment) the atom belongs to
        self.sets = set()  # names of the Sets this atom is in (CreateSet); copies keep them
        self.Charge = 0.0
        self.FormalCharge = 0

    def clone(self, offset=0):
        atom = _Atom(self.ElementSymbol, self.XYZ, self.molecule + offset)
        atom.sets = set(self.sets)
        atom.Charge, atom.FormalCharge = self.Charge, self.FormalCharge
        return atom


class _Atoms(list):
    @property
    def Count(self):
        return len(self)


class _Molecule:
    """A connected fragment of a document (the Molecules filter): NumAtoms, Atoms, Delete (Molecule class docs)."""
    def __init__(self, doc, key):
        self._doc, self._key = doc, key
        self.Name = "Molecule%d" % key

    @property
    def Atoms(self):
        return _Atoms(a for a in self._doc.Atoms if a.molecule == self._key)

    @property
    def NumAtoms(self):
        return len(self.Atoms)

    def Delete(self):
        self._doc.Atoms = _Atoms(a for a in self._doc.Atoms if a.molecule != self._key)


class _Filter:
    """UnitCell / AsymmetricUnit / DisplayRange view of a document."""
    def __init__(self, doc):
        self._doc = doc

    @property
    def Atoms(self):
        return self._doc.Atoms

    def Sets(self, name):
        """A named Set (CreateSet): .Atoms, and .Atoms.Delete() removes them from the document."""
        members = [a for a in self._doc.Atoms if name in a.sets]
        if not members:
            raise RuntimeError("no Set named %r in %s" % (name, self._doc.Name))
        return _Set(self._doc, name)

    @property
    def Molecules(self):
        keys = []
        for atom in self._doc.Atoms:
            if atom.molecule not in keys:
                keys.append(atom.molecule)
        return [_Molecule(self._doc, key) for key in keys]


class _SetAtoms(_Atoms):
    def __init__(self, doc, name):
        super().__init__(a for a in doc.Atoms if name in a.sets)
        self._doc, self._name = doc, name

    def Delete(self):
        self._doc.Atoms = _Atoms(a for a in self._doc.Atoms if self._name not in a.sets)


class _Set:
    def __init__(self, doc, name):
        self._doc, self.Name = doc, name

    @property
    def Atoms(self):
        return _SetAtoms(self._doc, self.Name)


def _read_mol(path):
    """Atoms of a MOL V2000 file, each tagged with its connected fragment (so ions are separate molecules)."""
    with open(path) as handle:
        lines = handle.read().splitlines()
    n_atoms, n_bonds = int(lines[3][0:3]), int(lines[3][3:6])
    atoms = []
    for line in lines[4:4 + n_atoms]:
        x, y, z, element = line.split()[:4]
        atoms.append(_Atom(element, Point(float(x), float(y), float(z))))
    parent = list(range(n_atoms))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for line in lines[4 + n_atoms:4 + n_atoms + n_bonds]:
        a, b = int(line[0:3]) - 1, int(line[3:6]) - 1
        parent[root(a)] = root(b)
    for i, atom in enumerate(atoms):
        atom.molecule = root(i)
    return atoms


class _Symmetry:
    Density = 1.12


class _Lattice:
    CellVolume = 21000.0
    LengthA = LengthB = LengthC = 27.6
    AngleAlpha = AngleBeta = AngleGamma = 90.0

    def __init__(self, a=None, b=None, c=None):
        if a is not None:
            self.LengthA, self.LengthB, self.LengthC = a, b, c
            self.CellVolume = a * b * c


def _read_pdb(path):
    """Atoms of a PDB file: the ATOM records are one molecule (the protein), each HETATM residue its own
    (ions such as Ca2+ are separate molecules in Materials Studio too)."""
    atoms = []
    with open(path) as handle:
        for line in handle:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            element = line[76:78].strip() if len(line) >= 78 else ""
            element = (element or line[12:16].strip()[:1]).capitalize()
            key = 0 if line.startswith("ATOM") else 1000 + int(line[22:26])
            atoms.append(_Atom(element, Point(float(line[30:38]), float(line[38:46]), float(line[46:54])), key))
    return atoms


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
        if kind not in ("Single", "Aromatic", "Partial double", "Double", "Triple"):
            raise RuntimeError("CreateBond: invalid bond type %r" % kind)
        if a is b:
            raise RuntimeError("CreateBond: needs two different atoms")

    def CreateSet(self, name, items):
        items = list(items)
        if not items:
            raise RuntimeError("CreateSet: at least one object is needed")
        for atom in items:
            atom.sets.add(name)

    def UnbuildCrystal(self):
        """Back to the defining objects: for a P1 cell, the same atoms at the same places, no lattice."""
        for name in ("SymmetrySystem", "Lattice3D"):
            if name in self.__dict__:
                del self.__dict__[name]

    def Clean(self):
        pass

    def CreateRepeatUnit(self, head, tail, chiral=None):
        return {"doc": self, "head": head, "tail": tail, "chiral": chiral}

    def CopyFrom(self, other):
        self.Atoms = _Atoms(a.clone() for a in other.Atoms)  # a copy: deleting here leaves the source intact
        if hasattr(other, "SymmetrySystem"):
            self.SymmetrySystem, self.Lattice3D = other.SymmetrySystem, other.Lattice3D

    @property
    def UnitCell(self):
        return _Filter(self)

    AsymmetricUnit = DisplayRange = UnitCell

    @property
    def Molecules(self):
        return _Filter(self).Molecules

    def Save(self):
        pass

    def Discard(self):
        pass

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
        doc = self.New(os.path.basename(filename))
        if filename.lower().endswith(".mol"):
            doc.Atoms = _Atoms(_read_mol(filename))
        elif filename.lower().endswith(".pdb"):
            doc.Atoms = _Atoms(_read_pdb(filename))
        return doc

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
    def __init__(self, forcite=None):
        self.forcite = forcite

    def Run(self, doc, settings=None):
        time.sleep(0.01)
        doc.PotentialEnergy = -1500.0
        traj = _Doc(doc.Name.replace(".xsd", ".xtd"))
        traj.CopyFrom(doc)  # every frame has the document's atoms
        traj.Trajectory = _TrajectoryInfo()
        chosen = self.forcite.settings if self.forcite else {}
        if chosen.get("NumberOfSteps") and chosen.get("TrajectoryFrequency"):
            traj.Trajectory.NumFrames = int(chosen["NumberOfSteps"]) // int(chosen["TrajectoryFrequency"]) + 1
        traj.Trajectory.CurrentFrame = 1
        _write_job_file(traj.Name)
        _write_job_file(doc.Name.replace(".xsd", ".trj"), "binary-ish")
        return _Results(Density=1.13, CellVolume=20950.0, Temperature=298.4, Pressure=0.0002, PotentialEnergy=-1500.0,
                        Trajectory=traj, Report=_Report("Forcite Dynamics (fake report)\n"))


class _Energy:
    def Run(self, doc, settings=None):
        """Placeholder energy (not physics): depends on the atoms, so parts differ from the whole.
        "Forcefield assigned" charges = each atom's formal charge, except ions (Ca) get 0 - what pcff did to
        Savinase's Ca2+ on the VM; "Use current" keeps the charges already on the atoms."""
        if Modules.Forcite.settings.get("ChargeAssignment", "Forcefield assigned") == "Forcefield assigned":
            for atom in doc.Atoms:
                atom.Charge = 0.0 if atom.ElementSymbol == "Ca" else float(atom.FormalCharge or 0)
        doc.PotentialEnergy = -0.5 * doc.Atoms.Count - 0.001 * doc.Atoms.Count ** 2
        return _Results(Structure=doc)


class _CohesiveEnergyDensity:
    def Run(self, doc, settings=None):
        _write_job_file(doc.Name.replace(".xtd", "") + " CED.txt")
        return _Results(CohesiveEnergyDensity=4.0e8, SolubilityParameter=20.0,
                        Report=_Report("Forcite Cohesive Energy Density (fake report)\n"))


class _Forcite(_Configurable):
    def __init__(self):
        super().__init__()
        self.GeometryOptimization = _GeometryOptimization()
        self.Dynamics = _Dynamics(self)
        self.Energy = _Energy()
        self.CohesiveEnergyDensity = _CohesiveEnergyDensity()


class _Construction:
    def __init__(self, module=None):
        self.module = module
        self.components = []
        self.Loading = {}

    def AddComponent(self, doc):
        self.components.append(doc)

    def RemoveComponent(self, doc):
        self.components.remove(doc)
        self.Loading.pop(doc, None)

    def Run(self, settings=None):
        cell = _Doc("Construction.xtd")
        offset = 0
        for doc in self.components:
            span = max([a.molecule for a in doc.Atoms] + [0]) + 1
            for _ in range(self.Loading[doc]):
                cell.Atoms.extend(a.clone(offset) for a in doc.Atoms)  # every copy is its own molecule(s)
                offset += span
        cell.SymmetrySystem, cell.Lattice3D = _Symmetry(), self._lattice(cell)
        _write_job_file(cell.Name)
        return _Results(Trajectory=cell, Report=_Report("Amorphous Cell Construction (fake report)\n"))

    def _lattice(self, cell):
        return _Lattice()


class _ConfinedLayer(_Construction):
    """Confined Layer: an Orthorhombic cell keeps the LengthA/LengthB it was given; c follows from the mass and
    TargetDensity (the ConfinedLayer settings docs)."""
    def _lattice(self, cell):
        settings = self.module.settings if self.module else {}
        if settings.get("LatticeType") != "Orthorhombic" or not settings.get("LengthA"):
            return _Lattice()
        a, b = float(settings["LengthA"]), float(settings["LengthB"])
        mass = sum(atom.Mass for atom in cell.Atoms)
        return _Lattice(a, b, mass / (float(settings.get("TargetDensity", 1.0)) * 0.602214076 * a * b))


class _AmorphousCell(_Configurable):
    def __init__(self):
        super().__init__()
        self.Construction = _Construction(self)
        self.ConfinedLayer = _ConfinedLayer(self)


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


class _CrystalBuilder(_Configurable):
    """Crystal task: SetSpaceGroup, SetCellParameters, Build(doc) makes the document 3D periodic in place."""
    def __init__(self):
        super().__init__()
        self.cell = None

    def SetSpaceGroup(self, name, qualifier=""):
        self.group = name

    def SetCellParameters(self, a, b, c, alpha, beta, gamma):
        self.cell = (float(a), float(b), float(c))

    def Build(self, doc):
        if self.cell is None:
            raise RuntimeError("CrystalBuilder.Build: no cell parameters set")
        doc.SymmetrySystem, doc.Lattice3D = _Symmetry(), _Lattice(*self.cell)


class _LayerBuilder(_Configurable):
    """Layers task: SetLayer(n, doc, set name, vacuum, ...), Build() stacks the layers' atoms in a new document
    (only periodic documents can be layers, as in Materials Studio)."""
    def __init__(self):
        super().__init__()
        self.layers = {}

    def ClearLayers(self):
        self.layers = {}

    def SetLayer(self, number, doc, set_name, vacuum=0.0, cleave="Default", flip="No", origin_a=0.0, origin_b=0.0):
        if not hasattr(doc, "Lattice3D"):
            raise RuntimeError("SetLayer: %s is not periodic (only crystals or surfaces can be layers)" % doc.Name)
        self.layers[number] = (doc, set_name, float(vacuum))

    def Build(self, settings=None):
        if not self.layers:
            raise RuntimeError("LayerBuilder.Build: no layers defined")
        out = Documents.New("Layers.xsd")
        offset, c = 0, 0.0
        for number in sorted(self.layers):
            doc, _, vacuum = self.layers[number]
            span = max([a.molecule for a in doc.Atoms] + [0]) + 1
            out.Atoms.extend(a.clone(offset) for a in doc.Atoms)
            offset += span
            c += doc.Lattice3D.LengthC + vacuum
        first = self.layers[min(self.layers)][0].Lattice3D
        out.SymmetrySystem, out.Lattice3D = _Symmetry(), _Lattice(first.LengthA, first.LengthB, c)
        return out


class _Modules:
    Forcite = _Forcite()
    AmorphousCell = _AmorphousCell()


class _Tools:
    PolymerBuilder = _PolymerBuilder()
    CrystalBuilder = _CrystalBuilder()
    LayerBuilder = _LayerBuilder()


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
