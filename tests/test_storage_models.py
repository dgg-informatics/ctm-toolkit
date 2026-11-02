"""Tests for the MongoDB-boundary validation (ctm.schemas.storage).

Validation runs inside stamp(), so these cover both the models directly and the
guarantee that stamp() rejects a malformed document.
"""
import pytest
from pydantic import ValidationError


def _envelope(**over):
    base = {
        "trial_key": "2021.070",
        "trial_hash": "a" * 64,
        "processed_with": "ctm-mm trials 1.2.0",
        "run_date": "2026-08-21",
    }
    return {**base, **over}


def test_a_well_formed_envelope_validates():
    from ctm.schemas.storage import validate_storage

    validate_storage(_envelope())          # must not raise
    validate_storage(_envelope(diff_status="changed"))


def test_diff_status_is_an_enum_not_a_free_string():
    """It is the only routing signal ctm-llm general filters on, so a typo must not
    pass silently."""
    from ctm.schemas.storage import validate_storage

    with pytest.raises(ValidationError):
        validate_storage(_envelope(diff_status="chnaged"))


def test_trial_hash_must_be_a_sha256_digest():
    """The join key between 00_raw_trials and 01_normalized_trials, and the unique
    key of every collection."""
    from ctm.schemas.storage import validate_storage

    with pytest.raises(ValidationError, match="sha256"):
        validate_storage(_envelope(trial_hash="deadbeef"))
    with pytest.raises(ValidationError, match="sha256"):
        validate_storage(_envelope(trial_hash="A" * 64))  # uppercase is not hex-digest form


def test_run_date_must_be_iso():
    """Inherited down the chain, so a bad value misdates every later stage."""
    from ctm.schemas.storage import validate_storage

    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        validate_storage(_envelope(run_date="08/21/2026"))


def test_missing_envelope_field_is_rejected():
    from ctm.schemas.storage import validate_storage

    incomplete = _envelope()
    del incomplete["run_date"]
    with pytest.raises(ValidationError):
        validate_storage(incomplete)


def test_empty_trial_key_is_rejected():
    from ctm.schemas.storage import validate_storage

    with pytest.raises(ValidationError):
        validate_storage(_envelope(trial_key="  "))


def test_llm_curation_alias_round_trips():
    """The stored key _ctml_suggestions has a leading underscore Pydantic treats as
    private; it must validate from the stored spelling."""
    from ctm.schemas.storage import LlmCuration

    curation = LlmCuration.model_validate({
        "_ctml_suggestions": [
            {"source": "inclusion", "text": "Age >= 18",
             "suggested_node": {"clinical": {"age_numerical": ">=18"}},
             "transferred_to_match": False},
        ],
        "biomarker_references": [],
    })
    assert len(curation.ctml_suggestions) == 1
    assert curation.ctml_suggestions[0].source == "inclusion"


def test_llm_curation_validated_through_stamp():
    """A malformed _llm_curation is caught at the write boundary, not downstream."""
    from ctm.schemas.storage import validate_storage

    doc = _envelope(diff_status="changed")
    doc["_llm_curation"] = {"_ctml_suggestions": [{"text": "missing source"}]}

    with pytest.raises(ValidationError):
        validate_storage(doc)


def test_llm_curation_both_keys_optional():
    """Each LLM stage owns one key and merges, so a doc mid-chain has only one."""
    from ctm.schemas.storage import validate_storage

    only_general = _envelope()
    only_general["_llm_curation"] = {"_ctml_suggestions": [
        {"source": "summary", "text": "A BRCA1 trial"}]}
    validate_storage(only_general)

    only_biomarkers = _envelope()
    only_biomarkers["_llm_curation"] = {"biomarker_references": [
        {"trial_nct": "NCT1", "reference": "BRCA1", "biomarker": "BRCA1", "type": "snv"}]}
    validate_storage(only_biomarkers)


def test_curation_allows_extra_body_fields():
    """The body may grow; the model guards only what downstream reads."""
    from ctm.schemas.storage import LlmCuration

    curation = LlmCuration.model_validate({
        "_ctml_suggestions": [],
        "biomarker_references": [],
        "final_suggested_ctml": [{"clinical": {}}],  # legacy key, must not be rejected
    })
    assert curation.ctml_suggestions == []


# ── enforcement at the stamp() boundary ──────────────────────────────────────

def test_stamp_validates_the_document_it_produces(monkeypatch):
    """stamp() itself is the enforcement point, so every stored doc is checked."""
    from ctm import db as ctm_db

    trial = {"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
             "trial_hash": "a" * 64, "_raw": {"status": "open"}}
    stamped = ctm_db.stamp(trial, "ctm-mm trials", "2026-08-21")

    assert stamped["run_date"] == "2026-08-21"
    assert stamped["trial_key"] == "2021.070"


def test_stamp_rejects_a_bad_run_date():
    from ctm import db as ctm_db

    trial = {"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
             "trial_hash": "a" * 64, "_raw": {}}
    with pytest.raises(ValidationError):
        ctm_db.stamp(trial, "ctm-mm trials", "not-a-date")


def test_stamp_does_not_alter_the_document_it_validates():
    """Validation constructs and discards; the stored dict must be unchanged."""
    from ctm import db as ctm_db

    trial = {"entity": "amc", "protocol_no": "2021.070", "nct_id": None,
             "trial_hash": "a" * 64, "_raw": {},
             "_llm_curation": {"_ctml_suggestions": [], "biomarker_references": [],
                               "final_suggested_ctml": [{"clinical": {}}]}}
    stamped = ctm_db.stamp(trial, "ctm-mm trials-curate", "2026-08-21")

    # Every original key survives, including the legacy one the model ignores.
    assert stamped["_llm_curation"]["final_suggested_ctml"] == [{"clinical": {}}]


# ── 2.4.0 clinical/genomic shape ─────────────────────────────────────────────

def test_llm_curation_accepts_new_shape():
    from ctm.schemas.storage import LlmCuration

    entry = {"source": "inclusion", "text": "HER2+ breast", "suggested_node": {"clinical": {}},
             "transferred_to_match": False, "node_type": "clinical", "mixed": True}
    null = {**entry, "suggested_node": None, "node_type": "genomic", "mixed": False}
    ref = {"trial_nct": "NCT1", "reference": "HER2+", "biomarker": "ERBB2", "type": "ihc",
           "section": "inclusion", "in_kb": True, "origin": "somatic"}
    curation = LlmCuration.model_validate({
        "_ctml_suggestions": [entry], "_null_ctml_suggestions": [null],
        "molecular_genomic_refs": [ref],
        "parsed_sections": {"clinical": {"inclusion": {"found": True, "scanned": True, "pieces": 2}}},
    })
    assert curation.ctml_suggestions[0].node_type == "clinical"
    assert curation.ctml_suggestions[0].mixed is True
    dumped = curation.model_dump(by_alias=True)
    assert dumped["_null_ctml_suggestions"][0]["node_type"] == "genomic"
    assert dumped["molecular_genomic_refs"][0]["origin"] == "somatic"
    assert dumped["parsed_sections"]["clinical"]["inclusion"]["pieces"] == 2

    with pytest.raises(ValidationError):   # node_type is clinical | genomic only
        LlmCuration.model_validate({"_ctml_suggestions": [{**entry, "node_type": "other"}]})
    with pytest.raises(ValidationError):   # null entries are validated like the others
        LlmCuration.model_validate({"_null_ctml_suggestions": [{"source": "x"}]})
    with pytest.raises(ValidationError):   # refs require section
        LlmCuration.model_validate({"molecular_genomic_refs": [
            {k: v for k, v in ref.items() if k != "section"}]})


def test_molecular_ref_origin():
    from ctm.schemas.storage import MolecularGenomicRef

    base = {"trial_nct": "NCT1", "reference": "r", "biomarker": "B", "type": "snv",
            "section": "inclusion"}
    assert MolecularGenomicRef.model_validate(base).origin == "unknown"
    for origin in ("germline", "somatic"):
        assert MolecularGenomicRef.model_validate({**base, "origin": origin}).origin == origin
    with pytest.raises(ValidationError):
        MolecularGenomicRef.model_validate({**base, "origin": "maybe"})


def test_llm_curation_still_accepts_legacy_shape():
    from ctm.schemas.storage import LlmCuration

    curation = LlmCuration.model_validate({
        "_ctml_suggestions": [{"source": "inclusion", "text": "Age >= 18",
                               "suggested_node": {"clinical": {"age_numerical": ">=18"}}}],
        "biomarker_references": [{"biomarker": "BRCA1", "section": "titles"}],
        "final_suggested_ctml": [{"clinical": {}}],
    })
    assert curation.ctml_suggestions[0].node_type is None
