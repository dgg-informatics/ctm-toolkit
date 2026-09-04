"""Prepare `ctm-mm patients` output for ingestion into MongoDB.

The output is ``{clinical: [...], genomic: [...], extras: {patients: {...}}}``. This
module turns it into Mongo-ready documents — assigning clinical ``_id``s and linking
each genomic doc to its clinical doc via ``CLINICAL_ID`` (what matchengine's own
``map_clinical_to_genomic`` does at load time) — and can split it to disk as one JSON
object per file for a matchengine-loadable fallback.

Pure: no Mongo, no I/O beyond the explicit disk export. Callers handle the writes.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from bson import ObjectId

DOC_SETS = ("clinical", "genomic", "patient_data")


@dataclass
class PreparedLoad:
    clinical: list[dict]        # each with an assigned _id
    genomic: list[dict]         # each linked to its clinical via CLINICAL_ID
    patient_data: list[dict]    # one doc per patient, from extras.patients
    orphans: list[str]          # genomic SAMPLE_IDs with no clinical doc


def _shape_patient_data(extras: dict) -> list[dict]:
    """extras.patients (keyed by SAMPLE_ID) → one doc per patient, SAMPLE_ID promoted."""
    patients = (extras or {}).get("patients") or {}
    out = []
    for sample_id, entry in patients.items():
        out.append({"SAMPLE_ID": sample_id, **entry})
    return out


def prepare(data: dict) -> PreparedLoad:
    """Mongo-ready docs from a ctm-mm patients JSON payload.

    Assigns each clinical doc an ObjectId _id, then stamps every genomic doc with
    CLINICAL_ID = the clinical _id sharing its SAMPLE_ID. A genomic doc whose
    SAMPLE_ID has no clinical doc is kept (no data loss) without a CLINICAL_ID and
    reported in ``orphans``.
    """
    if not isinstance(data, dict) or "clinical" not in data or "genomic" not in data:
        raise ValueError(
            "pt-data JSON must be an object with 'clinical' and 'genomic' arrays "
            "(the output of `ctm-mm patients`)"
        )

    sample_to_id: dict[str, ObjectId] = {}
    clinical: list[dict] = []
    for c in data["clinical"]:
        c = {**c, "_id": ObjectId()}
        sample_to_id[c.get("SAMPLE_ID")] = c["_id"]
        clinical.append(c)

    genomic: list[dict] = []
    orphans: set[str] = set()
    for g in data["genomic"]:
        g = dict(g)
        clinical_id = sample_to_id.get(g.get("SAMPLE_ID"))
        if clinical_id is None:
            orphans.add(g.get("SAMPLE_ID"))
        else:
            g["CLINICAL_ID"] = clinical_id
        genomic.append(g)

    patient_data = _shape_patient_data(data.get("extras", {}))
    return PreparedLoad(clinical, genomic, patient_data, sorted(o for o in orphans if o))


def export_to_disk(data: dict, out_dir: Path) -> dict[str, int]:
    """Split the raw payload into clinical/, genomic/, patient_data/ folders under
    ``out_dir``, one JSON object per file — the directory-of-single-objects form
    ``matchengine load -c/-g`` reads. Uses the raw docs (no _id/CLINICAL_ID);
    matchengine assigns its own _id and re-links by SAMPLE_ID on load.

    Returns the count written per set.
    """
    sets = {
        "clinical": data.get("clinical", []),
        "genomic": data.get("genomic", []),
        "patient_data": _shape_patient_data(data.get("extras", {})),
    }
    counts = {}
    for name, docs in sets.items():
        folder = out_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for i, doc in enumerate(docs):
            (folder / f"{i:05d}.json").write_text(json.dumps(doc, default=str))
        counts[name] = len(docs)
    return counts
