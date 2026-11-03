"""ctm-llm autofill-ctml: the one writer of treatment_list.step[0].match."""
import copy

from ctm.transformers.autofill_ctml import CURATED_METHOD_KEY, autofill_trial


def _e(source, text, node, node_type, **extra):
    return {"source": source, "text": text, "suggested_node": node, "transferred_to_match": None,
            "node_type": node_type, "mixed": False, **extra}


def _dx(name):
    return {"clinical": {"oncotree_primary_diagnosis": name}}


AUTO = {CURATED_METHOD_KEY: "auto"}
AGE = {"clinical": {"age_numerical": ">=18"}}
KRAS_NO = {"genomic": {"hugo_symbol": "KRAS", "variant_category": "!Mutation", "wildtype": False}}


def _trial(entries, match=None, nulls=()):
    return {"nct_id": "NCT1",
            "treatment_list": {"step": [{"match": [] if match is None else match, "arm": []}]},
            "_llm_curation": {"_ctml_suggestions": entries, "_null_ctml_suggestions": list(nulls)}}


def _auto(node):
    """node with the auto marker on every clinical/genomic leaf."""
    if "and" in node or "or" in node:
        key = "and" if "and" in node else "or"
        return {key: [_auto(c) for c in node[key]]}
    (kind, fields), = node.items()
    return {kind: {**fields, **AUTO}}


def _match(trial):
    return trial["treatment_list"]["step"][0]["match"]


def test_match_is_a_plain_list_of_nodes_with_the_auto_marker():
    trial = _trial([_e("eligibility.inclusion", "Breast cancer", _dx("Breast"), "clinical"),
                    _e("eligibility.exclusion", "Known KRAS mutation", KRAS_NO, "genomic"),
                    _e("_raw.minimum_age", "18 Years", AGE, "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("Breast")), _auto(KRAS_NO), _auto(AGE)]
    assert report["filled"] is True and report["nodes"] == 3
    assert report["diagnosis_from"] == "criteria"


def test_single_node_is_a_one_item_list():
    out, _ = autofill_trial(_trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")]))
    assert _match(out) == [_auto(AGE)]


def test_criteria_diagnosis_wins_over_fallback_sources():
    trial = _trial([_e("eligibility.inclusion", "Invasive ductal carcinoma", _dx("Breast Invasive Ductal Carcinoma"), "clinical"),
                    _e("_raw.conditions", "Breast Cancer", _dx("Breast"), "clinical"),
                    _e("_raw.disease_site", "Breast", _dx("Breast"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("Breast Invasive Ductal Carcinoma"))]
    assert report["diagnosis_from"] == "criteria"


def test_negated_criteria_diagnosis_does_not_count_as_a_diagnosis():
    trial = _trial([_e("eligibility.exclusion", "No lung cancer", _dx("!Lung"), "clinical"),
                    _e("_raw.disease_site", "Breast", _dx("Breast"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("!Lung")), _auto(_dx("Breast"))]
    assert report["diagnosis_from"] == "fallback"


def test_fallback_diagnoses_are_one_or_of_distinct_names():
    trial = _trial([_e("_summary.long_title", "A Breast and Lung study", {"or": [_dx("Breast"), _dx("Lung")]}, "clinical"),
                    _e("_raw.conditions", "Breast Cancer", _dx("Breast"), "clinical"),
                    _e("_raw.categorys", "Breast", _dx("Breast"), "clinical"),
                    _e("_raw.disease_site", "Lung", _dx("Lung"), "clinical")])
    out, _ = autofill_trial(trial)
    assert _match(out) == [{"or": [_auto(_dx("Breast")), _auto(_dx("Lung"))]}]


def test_genomic_fallback_only_without_criteria_genomic_nodes():
    brca = {"genomic": {"hugo_symbol": "BRCA1", "variant_category": "Mutation", "wildtype": False}}
    title = _e("_summary.long_title", "In BRCA1 carriers", brca, "genomic")
    out, _ = autofill_trial(_trial([title]))
    assert _match(out) == [_auto(brca)]
    out, _ = autofill_trial(_trial([title, _e("eligibility.exclusion", "Known KRAS mutation", KRAS_NO, "genomic")]))
    assert _match(out) == [_auto(KRAS_NO)]


def test_duplicate_nodes_are_collapsed():
    trial = _trial([_e("eligibility.inclusion", "Age >= 18", AGE, "clinical"),
                    _e("_raw.minimum_age", "18 Years", AGE, "clinical")])
    out, _ = autofill_trial(trial)
    assert _match(out) == [_auto(AGE)]


def test_used_entries_marked_transferred_and_mixed_counted():
    her2 = {"genomic": {"hugo_symbol": "ERBB2", "variant_category": "Copy Number Variation", "wildtype": False}}
    trial = _trial([_e("eligibility.inclusion", "HER2+ breast", _dx("Breast"), "clinical", mixed=True),
                    _e("eligibility.inclusion", "HER2+ breast", her2, "genomic", mixed=True),
                    _e("_raw.conditions", "Breast Cancer", _dx("Breast"), "clinical")])
    out, report = autofill_trial(trial)
    flags = {(e["node_type"], e["source"]): e["transferred_to_match"]
             for e in out["_llm_curation"]["_ctml_suggestions"]}
    assert flags == {("clinical", "eligibility.inclusion"): True, ("genomic", "eligibility.inclusion"): True,
                     ("clinical", "_raw.conditions"): False}
    assert [e["transferred_to_match"] for e in out["_llm_curation"]["_null_ctml_suggestions"]] == []
    assert report["mixed_pieces"] == 1
    assert out["_llm_curation"]["autofill"] == report


def test_existing_match_kept_unless_overwrite():
    curated = [{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")], match=copy.deepcopy(curated))
    out, report = autofill_trial(trial)
    assert _match(out) == curated
    assert report["filled"] is False and report["reason"] == "match not empty"
    assert out["_llm_curation"]["_ctml_suggestions"][0]["transferred_to_match"] is None   # never decided
    out, report = autofill_trial(trial, overwrite=True)
    assert _match(out) == [_auto(AGE)] and report["filled"] is True


def test_nothing_to_fill_and_legacy_entries_ignored():
    legacy = {"source": "eligibility.inclusion", "text": "Age >= 18", "suggested_node": AGE}
    out, report = autofill_trial(_trial([legacy], nulls=[_e("eligibility.inclusion", "x", None, "clinical")]))
    assert _match(out) == []
    assert report["filled"] is False and report["reason"] == "no suggestions"
    assert out["_llm_curation"]["_ctml_suggestions"] == [legacy]


def test_trial_without_a_step_is_left_alone():
    trial = {"nct_id": "NCT1", "_llm_curation": {"_ctml_suggestions": [_e("_raw.minimum_age", "18 Years", AGE, "clinical")]}}
    out, report = autofill_trial(trial)
    assert "treatment_list" not in out
    assert report["filled"] is False and report["reason"] == "no treatment_list step"


def test_autofill_does_not_mutate_input():
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")])
    before = copy.deepcopy(trial)
    autofill_trial(trial)
    assert trial == before


# ── CLI: ctm-llm autofill-ctml ───────────────────────────────────────────────

def _cli_trial():
    return _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")])


def test_cli_reads_the_genomic_collection_and_writes_its_own(fake_mongo, tmp_path, monkeypatch):
    import json

    from ctm import db as ctm_db
    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    fake_mongo["collections"] = {ctm_db.LLM_GENOMIC_COLLECTION: [_cli_trial()]}
    out = tmp_path / "auto.json"
    llm_cli.main(["autofill-ctml", "--out", str(out)])

    assert fake_mongo["read_from"][1] == ctm_db.LLM_GENOMIC_COLLECTION
    assert fake_mongo["prepared"]["name"] == ctm_db.LLM_AUTOFILL_COLLECTION == "04b_llm_autofill_trials"
    assert ctm_db.LLM_CLINICAL_COLLECTION == "03_clinical_trials"
    assert ctm_db.LLM_GENOMIC_COLLECTION == "04_genomic_trials"
    stored = fake_mongo["upserted"][0]["doc"]
    assert stored["treatment_list"]["step"][0]["match"] == [_auto(AGE)]
    assert stored["processed_with"].startswith("ctm-llm autofill-ctml ")
    assert json.loads(out.read_text())[0]["_llm_curation"]["autofill"]["filled"] is True


def test_cli_overwrite_flag(fake_mongo, tmp_path, monkeypatch):
    import json

    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    trial = _cli_trial()
    trial["treatment_list"]["step"][0]["match"] = [{"clinical": {"gender": "Female"}}]
    src = tmp_path / "in.json"
    src.write_text(json.dumps([trial]))
    out = tmp_path / "out.json"
    llm_cli.main(["autofill-ctml", "--trials", str(src), "--out", str(out)])
    assert json.loads(out.read_text())[0]["treatment_list"]["step"][0]["match"] == [{"clinical": {"gender": "Female"}}]
    llm_cli.main(["autofill-ctml", "--trials", str(src), "--out", str(out), "--overwrite"])
    assert json.loads(out.read_text())[0]["treatment_list"]["step"][0]["match"] == [_auto(AGE)]


def test_handoff_export_moves_from_genomic_to_autofill():
    from pathlib import Path

    from ctm.llm_cli import build_parser, default_export

    for stage, expected in (("genomic", None), ("autofill-ctml", "2026-11-02_llm-biomarkers_trials.json")):
        args = build_parser().parse_args([stage])
        path = default_export(args.command, "2026-11-02")
        assert (path.name if isinstance(path, Path) else path) == expected


def test_autofill_is_flagged_beta_and_machine_written():
    from ctm import db as ctm_db
    from ctm.llm_cli import build_parser

    sub = next(a for a in build_parser()._actions if a.dest == "command")
    assert "[BETA]" in sub.choices["autofill-ctml"].description
    assert ctm_db.LLM_AUTOFILL_COLLECTION in ctm_db.MACHINE_WRITTEN


def test_null_entries_are_marked_not_transferred():
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")],
                   nulls=[_e("eligibility.inclusion", "Able to consent", None, "clinical")])
    out, _ = autofill_trial(trial)
    assert out["_llm_curation"]["_null_ctml_suggestions"][0]["transferred_to_match"] is False


def test_ecog_is_suggested_but_never_written_to_the_match():
    ecog = {"clinical": {"ecog_score": "<=2"}}
    age_and_ecog = {"clinical": {"age_numerical": ">=18", "ecog_score": "<=1"}}
    trial = _trial([_e("eligibility.inclusion", "ECOG 0-2", ecog, "clinical"),
                    _e("eligibility.inclusion", "Adults with ECOG 0-1", age_and_ecog, "clinical")])
    out, _ = autofill_trial(trial)
    assert _match(out) == [_auto(AGE)]
    flags = [e["transferred_to_match"] for e in out["_llm_curation"]["_ctml_suggestions"]]
    assert flags == [False, True]
    assert out["_llm_curation"]["_ctml_suggestions"][0]["suggested_node"] == ecog   # suggestion kept
