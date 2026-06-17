"""Pipeline integration tests — requires MongoDB."""
import json
import pytest
from pathlib import Path
from ctm.pipeline import run_pipeline, generate_run_id
from ctm.db.store import get_run, get_processed_documents


def test_generate_run_id_format():
    run_id = generate_run_id()
    # Format: DDMmmYYYY-8hexchars e.g. "17Jun2026-a3f8c201"
    date_part, hex_part = run_id.split("-")
    assert len(hex_part) == 8
    assert all(c in "0123456789abcdef" for c in hex_part)
    assert len(date_part) >= 8  # e.g. "17Jun2026"


def test_run_pipeline_creates_output(tmp_path, db):
    # Set up raw input directory
    raw_dir = tmp_path / "raw"
    ct_dir = raw_dir / "clinical_trials"
    pg_dir = raw_dir / "patient_general"
    gn_dir = raw_dir / "patient_genetic"
    ct_dir.mkdir(parents=True)
    pg_dir.mkdir(parents=True)
    gn_dir.mkdir(parents=True)

    (ct_dir / "trial.json").write_text(json.dumps({
        "nct_id": "NCT99999",
        "trial_summary_status": "open",
        "match_type": "gene",
    }))
    (pg_dir / "patient.json").write_text(json.dumps({
        "mrn": "TESTMRN",
        "gender": "Male",
        "vital_status": "alive",
    }))
    (gn_dir / "genetic.json").write_text(json.dumps({
        "sample_id": "samp001",
        "mrn": "TESTMRN",
        "true_hugo_symbol": "KRAS",
        "allele_fraction": 0.45,
        "tier": 1,
    }))

    output_base = tmp_path / "output"
    run_id = run_pipeline(
        str(raw_dir),
        generate_report=False,
        output_base=output_base,
        db=db,
    )

    # Output directory exists
    out_dir = output_base / run_id
    assert out_dir.is_dir()

    # Processed files written
    assert (out_dir / "clinical_trials.json").exists()
    assert (out_dir / "patient_general.json").exists()
    assert (out_dir / "patient_genetic.json").exists()

    # MongoDB run record stored
    run_doc = get_run(run_id, db=db)
    assert run_doc is not None
    assert run_doc["status"] == "completed"

    # Processed documents in MongoDB
    docs = get_processed_documents(run_id, db=db)
    source_types = {d["source_type"] for d in docs}
    assert "clinical_trials" in source_types
    assert "patient_general" in source_types
    assert "patient_genetic" in source_types


def test_run_pipeline_processed_content(tmp_path, db):
    raw_dir = tmp_path / "raw"
    pg_dir = raw_dir / "patient_general"
    pg_dir.mkdir(parents=True)

    (pg_dir / "patient.json").write_text(json.dumps({
        "mrn": "MRNABC",
        "gender": "Female",
        "vital_status": "alive",
        "oncotree_primary_diagnosis_name": "Breast Cancer",
        "tumor_mutational_burden_per_megabase": 8.2,
    }))

    output_base = tmp_path / "output"
    run_id = run_pipeline(str(raw_dir), generate_report=False, output_base=output_base, db=db)

    out_file = output_base / run_id / "patient_general.json"
    data = json.loads(out_file.read_text())
    assert data["mrn"] == "MRNABC"
    assert data["diagnosis"] == "Breast Cancer"
    assert data["tmb"] == 8.2
