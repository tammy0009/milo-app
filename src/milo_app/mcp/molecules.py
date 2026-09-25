"""MILO's molecule memory: molecules the user has given once, kept exactly, and copied into scripts by
the MCP itself (never retyped).

A molecule is saved from a SMILES or from a structure file. It is checked with RDKit, and its identity
(formula, weight, canonical SMILES, InChIKey) goes back to the user to confirm. What a script gets is
the stored structure file, word for word:

  from a SMILES   a 3D MOL file made by RDKit (ETKDG embedding, then MMFF94 or UFF), with every
                  hydrogen. It is read back and must be the same molecule (same InChIKey) as the SMILES,
                  stereo included.
  from a file     the file itself (.mol/.sdf/.pdb are read by RDKit for their identity; other formats,
                  like .xsd, are kept as they are and can only go to Materials Studio).

Every structure carries a checksum (FNV-1a, 32-bit, over its characters). The bundle writers check it on
the VM before using the molecule, so a script whose molecule changed on the way refuses to run.

Stored one file per molecule in <data>/molecules/<file_name>.json.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from milo_app.config import get_settings

EMBED_SEED = 0xF00D  # the same SMILES always gives the same 3D structure
# What each product can read, by extension (Materials Studio: Documents.Import; Discovery Studio: Open).
BOTH = {"mol", "sdf", "sd", "pdb", "mol2"}
MS_ONLY = {"xsd", "car", "cif", "xyz", "msi", "xtl", "cssr", "cml"}
PRODUCTS = {"materials_studio": BOTH | MS_ONLY, "discovery_studio": BOTH}
READ_BY_RDKIT = {"mol", "sdf", "sd", "pdb"}


def checksum(text: str) -> str:
    """FNV-1a (32-bit) over the characters; the bundle writers compute the same thing on the VM."""
    h = 0x811C9DC5
    for ch in text:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "fnv1a:%08x" % h


def _folder() -> Path:
    folder = get_settings().data_dir / "molecules"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "molecule"


def _all() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_folder().glob("*.json"))]


def find(name: str) -> dict[str, Any] | None:
    """A saved molecule by its name or one of its other names (case does not matter)."""
    wanted = name.strip().lower()
    for entry in _all():
        if wanted in [entry["name"].lower(), *(a.lower() for a in entry.get("aliases", []))]:
            return entry
    return None


def summary(entry: dict[str, Any]) -> dict[str, Any]:
    """What a molecule is, without its structure text."""
    return {k: v for k, v in entry.items() if k != "structure"}


def list_molecules() -> list[dict[str, Any]]:
    return [summary(e) for e in _all()]


def for_script(names: list[str], product: str) -> dict[str, dict[str, Any]]:
    """{name: what the bundle writer needs} for molecules going into a script for `product`."""
    out = {}
    for name in names:
        entry = find(name)
        if entry is None:
            raise ValueError(f"no molecule '{name}' in MILO's molecule memory: save it first (milo_save_molecule)")
        if entry["format"] not in PRODUCTS[product]:
            raise ValueError(f"molecule '{entry['name']}' is a .{entry['format']} file, which "
                             f"{product.replace('_', ' ').title()} cannot read")
        out[entry["name"]] = {k: entry.get(k) for k in ("file_name", "format", "structure", "checksum", "smiles",
                                                        "canonical_smiles", "formula", "inchikey")}
    return out


# ---------------------------------------------------------------------------- saving


def save(name: str, smiles: str = "", file_path: str = "", notes: str = "", aliases: list[str] | None = None,
         allow_undefined_stereo: bool = False, replace: bool = False) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("a molecule needs a name")
    if bool(smiles.strip()) == bool(file_path.strip()):
        raise ValueError("give exactly one of smiles or file_path")
    aliases = [a.strip() for a in aliases or [] if a.strip()]
    for label in [name, *aliases]:
        clash = find(label)
        if clash is not None and not (replace and clash["name"].lower() == name.lower()):
            raise ValueError(f"'{label}' is already the molecule '{clash['name']}' "
                             f"({clash.get('formula') or clash['format']}); pick another name, or replace=True "
                             "to change that molecule")
    entry = _from_smiles(smiles.strip(), name, allow_undefined_stereo) if smiles.strip() else _from_file(Path(file_path.strip()))
    old = find(name)
    file_name = old["file_name"] if old else _unique_file_name(name)
    entry = {"name": name, "aliases": aliases, "file_name": file_name, **entry,
             "checksum": checksum(entry["structure"]), "notes": notes,
             "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (_folder() / f"{file_name}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return summary(entry) | {"saved": True, "replaced": old is not None}


def _unique_file_name(name: str) -> str:
    base, taken = _file_name(name), {p.stem.lower() for p in _folder().glob("*.json")}
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def _identity(mol) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors

    return {
        "canonical_smiles": Chem.MolToSmiles(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "mol_weight": round(Descriptors.MolWt(mol), 4),
        "charge": Chem.GetFormalCharge(mol),
        "inchi": Chem.MolToInchi(mol),
        "inchikey": Chem.MolToInchiKey(mol),
        "atoms": Chem.AddHs(mol).GetNumAtoms(),
        "fragments": len(Chem.GetMolFrags(mol)),
    }


def _undefined_stereo(mol) -> list[str]:
    from rdkit import Chem

    return [f"{info.type.name.replace('_', ' ').lower()} at atom/bond {info.centeredOn}"
            for info in Chem.FindPotentialStereo(mol) if info.specified == Chem.StereoSpecified.Unspecified]


def _from_smiles(smiles: str, name: str, allow_undefined_stereo: bool) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit cannot read the SMILES {smiles!r}: check it with the user")
    identity = _identity(mol)  # from the SMILES itself: 3D coordinates must not decide the stereo
    undefined = _undefined_stereo(mol)
    if undefined and not allow_undefined_stereo:
        raise ValueError("the SMILES leaves stereo open (" + "; ".join(undefined) + "). A 3D structure has to pick "
                         "one arrangement, so ask the user which one and put it in the SMILES (@/@@, / \\), or "
                         "save with allow_undefined_stereo=True if any arrangement will do")
    with_h = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = EMBED_SEED
    if AllChem.EmbedMolecule(with_h, params) != 0:
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(with_h, params) != 0:
            raise ValueError("RDKit could not build a 3D structure for this SMILES")
    if AllChem.MMFFHasAllMoleculeParams(with_h):
        AllChem.MMFFOptimizeMolecule(with_h, maxIters=2000)
        relaxed = "MMFF94"
    else:
        AllChem.UFFOptimizeMolecule(with_h, maxIters=2000)
        relaxed = "UFF"
    with_h.SetProp("_Name", name.encode("ascii", "replace").decode())
    block = Chem.MolToMolBlock(with_h)
    # The 3D file must be the molecule the SMILES says, stereo included.
    back = Chem.MolFromMolBlock(block, removeHs=False)
    same = back is not None and (Chem.MolToInchiKey(back) == identity["inchikey"] or (
        undefined and Chem.MolToInchiKey(back).split("-")[0] == identity["inchikey"].split("-")[0]))
    if not same:
        raise ValueError("the 3D structure RDKit built is not the same molecule as the SMILES; not saved")
    return identity | {
        "smiles": smiles, "format": "mol", "structure": block, "source": "smiles",
        "made_by": f"RDKit {_rdkit_version()}: ETKDGv3 embedding (seed {EMBED_SEED}), {relaxed} relaxation",
        "undefined_stereo": undefined,
        "products": sorted(p for p, formats in PRODUCTS.items() if "mol" in formats),
    }


def _from_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"no file at {path} (it must be on this PC)")
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in BOTH | MS_ONLY:
        raise ValueError(f".{fmt} is not a structure format MILO passes on: use one of "
                         + ", ".join(sorted(BOTH | MS_ONLY)))
    text = path.read_text(encoding="utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    if not text.isascii():
        raise ValueError("the file has non-ASCII characters; save it as plain ASCII first")
    entry: dict[str, Any] = {"format": fmt, "structure": text, "source": str(path), "undefined_stereo": [],
                             "products": sorted(p for p, formats in PRODUCTS.items() if fmt in formats)}
    if fmt in READ_BY_RDKIT:
        from rdkit import Chem

        if fmt == "pdb":
            mol = Chem.MolFromPDBBlock(text, removeHs=False)
        else:
            mol = Chem.MolFromMolBlock(text.split("$$$$")[0], removeHs=False)
            records = len([r for r in text.split("$$$$") if r.strip()])
            if records > 1:
                entry["note"] = f"{records} molecules in the file; the whole file is kept, the identity is the first one's"
        if mol is None:
            raise ValueError(f"RDKit cannot read this .{fmt} file: check it before saving")
        entry |= _identity(Chem.RemoveHs(mol, sanitize=False) if mol.GetNumAtoms() else mol)
        entry["atoms"] = mol.GetNumAtoms()
        entry["made_by"] = f"the file as given; identity read by RDKit {_rdkit_version()}"
    else:
        entry["made_by"] = "the file as given; RDKit cannot read ." + fmt + ", so its identity is not checked"
    return entry


def _rdkit_version() -> str:
    from rdkit import rdBase

    return rdBase.rdkitVersion
