"""Tests for ctm-mm load — patient-data ingestion (patient_load.py + _cmd_load)."""
import json

import pytest
from bson import ObjectId

from ctm.patient_load import export_to_disk, prepare

PAYLOAD = {
    "clinical": [
        {"SAMPLE_ID": "pt_0000001", "GENDER": "Male"},
        {"SAMPLE_ID": "pt_0000002", "GENDER": "Female"},
    ],
    "genomic": [
        {"SAMPLE_ID": "pt_0000001", "TRUE_HUGO_SYMBOL": "KRAS", "VARIANT_CATEGORY": "MUTATION"},
        {"SAMPLE_ID": "pt_0000001", "TRUE_HUGO_SYMBOL": "TP53", "VARIANT_CATEGORY": "MUTATION"},
        {"SAMPLE_ID": "pt_0000002", "TRUE_HUGO_SYMBOL": "MSI", "VARIANT_CATEGORY": "SIGNATURE"},
    ],
    "extras": {"patients": {
        "pt_0000001": {"patient": {"pt_uuid": "pt_0000001"}, "reports": []},
        "pt_0000002": {"patient": {"pt_uuid": "pt_0000002"}, "reports": []},
    }},
}


def test_prepare_links_genomic_to_clinical_by_sample_id():
    p = prepare(PAYLOAD)
    by_sample = {c["SAMPLE_ID"]: c["_id"] for c in p.clinical}
    assert all(isinstance(cid, ObjectId) for cid in by_sample.values())
    for g in p.genomic:
        assert g["CLINICAL_ID"] == by_sample[g["SAMPLE_ID"]]
    assert p.orphans == []


def test_prepare_flags_orphan_genomic_without_clinical():
    payload = {**PAYLOAD, "genomic": PAYLOAD["genomic"] + [
        {"SAMPLE_ID": "pt_9999999", "TRUE_HUGO_SYMBOL": "BRAF", "VARIANT_CATEGORY": "MUTATION"},
    ]}
    p = prepare(payload)
    assert p.orphans == ["pt_9999999"]
    orphan = next(g for g in p.genomic if g["SAMPLE_ID"] == "pt_9999999")
    assert "CLINICAL_ID" not in orphan          # kept, but unlinked


def test_prepare_shapes_patient_data_one_per_patient():
    p = prepare(PAYLOAD)
    assert len(p.patient_data) == 2
    assert {d["SAMPLE_ID"] for d in p.patient_data} == {"pt_0000001", "pt_0000002"}
    assert all("patient" in d and "reports" in d for d in p.patient_data)


def test_prepare_does_not_mutate_input():
    payload = json.loads(json.dumps(PAYLOAD))
    prepare(payload)
    assert "_id" not in payload["clinical"][0]
    assert "CLINICAL_ID" not in payload["genomic"][0]


@pytest.mark.parametrize("bad", [{}, {"clinical": []}, {"genomic": []}, [1, 2]])
def test_prepare_rejects_malformed_payload(bad):
    with pytest.raises(ValueError, match=r"clinical.*genomic|genomic"):
        prepare(bad)


def test_export_to_disk_writes_one_file_per_doc(tmp_path):
    counts = export_to_disk(PAYLOAD, tmp_path)
    assert counts == {"clinical": 2, "genomic": 3, "patient_data": 2}
    assert len(list((tmp_path / "clinical").glob("*.json"))) == 2
    assert len(list((tmp_path / "genomic").glob("*.json"))) == 3
    assert len(list((tmp_path / "patient_data").glob("*.json"))) == 2
    # each file is a single JSON object, no _id/CLINICAL_ID leaked from the raw payload
    doc = json.loads(next((tmp_path / "genomic").glob("*.json")).read_text())
    assert isinstance(doc, dict) and "CLINICAL_ID" not in doc


# ── _cmd_load end-to-end against an in-memory fake db ─────────────────────────

class _FakeCollection:
    def __init__(self):
        self.docs = []

    def drop(self):
        self.docs = []

    def insert_many(self, docs):
        self.docs.extend(docs)


class _FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def _load_args(**over):
    import argparse
    d = {"pt_data": None, "patient_db": None, "run_date": "2026-09-04",
         "disk": False, "out_dir": None}
    return argparse.Namespace(**{**d, **over})


def test_cmd_load_writes_dated_and_latest_collections(tmp_path, monkeypatch):
    from ctm import mm_cli

    pt_file = tmp_path / "pts.json"
    pt_file.write_text(json.dumps(PAYLOAD))

    fake = _FakeDB()
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.setenv("MONGO_PATIENT_DBNAME", "patients_test")
    monkeypatch.delenv("MONGO_DBNAME", raising=False)          # load must not require it
    monkeypatch.setattr("ctm.db.get_database", lambda config, db=None: fake)

    mm_cli._cmd_load(_load_args(pt_data=str(pt_file)))

    # dated snapshot + refreshed latest, for all three sets
    for base, n in (("clinical", 2), ("genomic", 3), ("patient_data", 2)):
        assert len(fake[f"2026-09-04_{base}"].docs) == n
        assert len(fake[f"latest_{base}"].docs) == n
    # genomic docs carry CLINICAL_ID after the load
    assert all("CLINICAL_ID" in g for g in fake["latest_genomic"].docs)


def test_cmd_load_errors_without_patient_db(tmp_path, monkeypatch):
    from ctm import mm_cli

    pt_file = tmp_path / "pts.json"
    pt_file.write_text(json.dumps(PAYLOAD))
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.delenv("MONGO_PATIENT_DBNAME", raising=False)

    with pytest.raises(SystemExit):
        mm_cli._cmd_load(_load_args(pt_data=str(pt_file)))
