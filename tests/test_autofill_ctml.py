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
    assert report["diagnosis_sources"] == ["eligibility.inclusion"]


def test_every_diagnosis_from_every_source_is_one_or():
    """Head and neck trial from the 07 sample: the criterion's vague histology, the
    conditions' specific diagnosis and the disease site all go into one OR."""
    trial = _trial([_e("eligibility.inclusion", "SCC of the head/neck", _dx("Squamous Cell Carcinoma, NOS"), "clinical"),
                    _e("eligibility.inclusion", "ECOG 0-1", {"clinical": {"ecog_score": "<=1"}}, "clinical"),
                    _e("_raw.minimum_age", "18 Years", AGE, "clinical"),
                    _e("_raw.conditions", "Head and Neck Squamous Cell Carcinoma",
                       _dx("Head and Neck Squamous Cell Carcinoma"), "clinical"),
                    _e("_raw._ddots.disease_site", "Head and Neck", _dx("Head and Neck"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [
        {"or": [_auto(_dx("Squamous Cell Carcinoma, NOS")),
                _auto(_dx("Head and Neck Squamous Cell Carcinoma")),
                _auto(_dx("Head and Neck"))]},
        _auto(AGE),
    ]
    assert report["diagnosis_sources"] == ["eligibility.inclusion", "_raw.conditions",
                                           "_raw._ddots.disease_site"]
    flags = {e["source"]: e["transferred_to_match"] for e in out["_llm_curation"]["_ctml_suggestions"]
             if e["source"] != "eligibility.inclusion"}
    assert flags == {"_raw.minimum_age": True, "_raw.conditions": True, "_raw._ddots.disease_site": True}


def test_negated_diagnoses_stay_separate_and_anded():
    trial = _trial([_e("eligibility.exclusion", "No lung cancer", _dx("!Lung"), "clinical"),
                    _e("_raw.disease_site", "Breast", _dx("Breast"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("Breast")), _auto(_dx("!Lung"))]
    assert report["diagnosis_sources"] == ["_raw.disease_site"]


def test_diagnosis_is_lifted_out_of_a_criterion_node_with_other_fields():
    both = {"clinical": {"oncotree_primary_diagnosis": "Breast", "gender": "Female"}}
    nested = {"and": [_dx("Ovary"), {"clinical": {"age_numerical": ">=18"}}]}
    trial = _trial([_e("eligibility.inclusion", "Women with breast cancer", both, "clinical"),
                    _e("eligibility.inclusion", "Adults with ovarian cancer", nested, "clinical")])
    out, _ = autofill_trial(trial)
    assert _match(out) == [{"or": [_auto(_dx("Breast")), _auto(_dx("Ovary"))]},
                           _auto({"clinical": {"gender": "Female"}}), _auto(AGE)]


def test_duplicate_diagnoses_across_sources_appear_once():
    trial = _trial([_e("_summary.long_title", "A Breast and Lung study", {"or": [_dx("Breast"), _dx("Lung")]}, "clinical"),
                    _e("_raw.conditions", "Breast Cancer", _dx("Breast"), "clinical"),
                    _e("_raw.categorys", "Breast", _dx("Breast"), "clinical"),
                    _e("_raw.disease_site", "Lung", _dx("Lung"), "clinical")])
    out, _ = autofill_trial(trial)
    assert _match(out) == [{"or": [_auto(_dx("Breast")), _auto(_dx("Lung"))]}]


def test_title_and_conditions_genomic_nodes_are_always_added_once():
    brca = {"genomic": {"hugo_symbol": "BRCA1", "variant_category": "Mutation", "wildtype": False}}
    title = _e("_summary.long_title", "In BRCA1 carriers", brca, "genomic")
    out, _ = autofill_trial(_trial([title]))
    assert _match(out) == [_auto(brca)]
    out, _ = autofill_trial(_trial([title, _e("eligibility.exclusion", "Known KRAS mutation", KRAS_NO, "genomic")]))
    assert _match(out) == [_auto(KRAS_NO), _auto(brca)]
    same = _e("eligibility.inclusion", "BRCA1 mutation", brca, "genomic")
    out, _ = autofill_trial(_trial([same, title, _e("_raw.conditions", "BRCA1 carrier", brca, "genomic")]))
    assert _match(out) == [_auto(brca)]                       # duplicates collapse to one


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
    # conditions' Breast is the same diagnosis — merged into the OR, so it counts as used.
    assert flags == {("clinical", "eligibility.inclusion"): True, ("genomic", "eligibility.inclusion"): True,
                     ("clinical", "_raw.conditions"): True}
    assert [e["transferred_to_match"] for e in out["_llm_curation"]["_null_ctml_suggestions"]] == []
    assert report["mixed_pieces"] == 1
    assert out["_llm_curation"]["autofill"] == report


def test_existing_match_kept_unless_overwrite():
    curated = [{"clinical": {"oncotree_primary_diagnosis": "Melanoma"}}]
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")], match=copy.deepcopy(curated))
    out, report = autofill_trial(trial)
    assert _match(out) == curated
    assert report["filled"] is False and report["reason"] == "match not empty"
    assert report["match_sources"] == []
    assert out["_llm_curation"]["_ctml_suggestions"][0]["transferred_to_match"] is None   # never decided
    out, report = autofill_trial(trial, overwrite=True)
    assert _match(out) == [_auto(AGE)] and report["filled"] is True
    assert report["reason"] == "overwrote existing match"
    assert out["_llm_curation"]["autofill"]["reason"] == "overwrote existing match"


def test_filling_an_empty_match_says_so_even_with_overwrite():
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical")])
    for overwrite in (False, True):
        out, report = autofill_trial(trial, overwrite=overwrite)
        assert report["filled"] is True and report["reason"] == "filled empty match"
        assert out["_llm_curation"]["autofill"]["reason"] == "filled empty match"


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


# ── mixed pieces: and/or decided by a resolver (AI in the CLI) ───────────────

BRCA2 = {"genomic": {"hugo_symbol": "BRCA2", "variant_category": "Mutation", "wildtype": False}}
MIXED_TEXT = "Breast cancer or a germline BRCA2 mutation"


def _mixed_trial():
    return _trial([_e("eligibility.inclusion", MIXED_TEXT, _dx("Breast"), "clinical", mixed=True),
                   _e("eligibility.inclusion", MIXED_TEXT, BRCA2, "genomic", mixed=True),
                   _e("_raw._ddots.disease_site", "Breast", _dx("Breast"), "clinical"),
                   _e("_raw.minimum_age", "18 Years", AGE, "clinical")])


def test_mixed_or_piece_becomes_one_or_item_and_keeps_its_diagnosis():
    asked = []

    def resolver(text, clinical, genomic):
        asked.append((text, clinical, genomic))
        return "or"

    out, report = autofill_trial(_mixed_trial(), resolve_mixed=resolver)
    assert asked == [(MIXED_TEXT, _dx("Breast"), BRCA2)]
    assert _match(out) == [_auto(_dx("Breast")),                       # disease site's diagnosis
                           {"or": [_auto(_dx("Breast")), _auto(BRCA2)]},
                           _auto(AGE)]
    logic = {e["node_type"]: e.get("mixed_logic") for e in out["_llm_curation"]["_ctml_suggestions"]
             if e["text"] == MIXED_TEXT}
    assert logic == {"clinical": "or", "genomic": "or"}
    assert report["mixed_logic"] == {"and": 0, "or": 1, "unresolved": 0}
    assert report["diagnosis_sources"] == ["_raw._ddots.disease_site"]


def test_mixed_and_piece_is_anded_as_before():
    out, report = autofill_trial(_mixed_trial(), resolve_mixed=lambda *a: "and")
    assert _match(out) == [_auto(_dx("Breast")), _auto(BRCA2), _auto(AGE)]
    assert report["mixed_logic"] == {"and": 1, "or": 0, "unresolved": 0}


def test_no_resolver_or_unresolved_falls_back_to_and_and_says_so():
    for kwargs in ({}, {"resolve_mixed": lambda *a: "unresolved"}):
        out, report = autofill_trial(_mixed_trial(), **kwargs)
        assert _match(out) == [_auto(_dx("Breast")), _auto(BRCA2), _auto(AGE)]
        assert report["mixed_logic"] == {"and": 0, "or": 0, "unresolved": 1}
        assert {e.get("mixed_logic") for e in out["_llm_curation"]["_ctml_suggestions"]
                if e["text"] == MIXED_TEXT} == {"unresolved"}


def test_resolver_not_called_without_mixed_criteria():
    def boom(*a):
        raise AssertionError("no mixed piece — must not call")

    autofill_trial(_trial([_e("eligibility.inclusion", "Breast", _dx("Breast"), "clinical")]), resolve_mixed=boom)


def test_ecog_only_clinical_side_of_an_or_piece_leaves_the_genomic_node():
    trial = _trial([_e("eligibility.inclusion", "ECOG 0-1 or BRCA2", {"clinical": {"ecog_score": "<=1"}},
                       "clinical", mixed=True),
                    _e("eligibility.inclusion", "ECOG 0-1 or BRCA2", BRCA2, "genomic", mixed=True)])
    out, _ = autofill_trial(trial, resolve_mixed=lambda *a: "or")
    assert _match(out) == [_auto(BRCA2)]


# ── the AI resolver ──────────────────────────────────────────────────────────

def test_ai_resolver_asks_once_and_caches():
    from llm_fakes import QueuedClient

    from ctm.transformers.autofill_ctml import ai_mixed_resolver

    client, cache = QueuedClient(['{"logic": "or"}']), {}
    resolve = ai_mixed_resolver(lambda: client, cache)
    assert resolve(MIXED_TEXT, _dx("Breast"), BRCA2) == "or"
    assert resolve(MIXED_TEXT, _dx("Breast"), BRCA2) == "or"
    assert client.call_count == 1 and len(cache) == 1
    assert MIXED_TEXT in client.prompts[0] and "BRCA2" in client.prompts[0]


def test_ai_resolver_junk_and_content_filter_are_unresolved():
    from llm_fakes import ContentFilterError, QueuedClient

    from ctm.transformers.autofill_ctml import ai_mixed_resolver

    for answer in ("not json", '{"logic": "maybe"}', "[]", None):
        resolve = ai_mixed_resolver(lambda a=answer: QueuedClient([a]), {})
        assert resolve("t", _dx("Breast"), BRCA2) == "unresolved"
    cache = {}
    resolve = ai_mixed_resolver(lambda: QueuedClient([ContentFilterError("blocked")]), cache)
    assert resolve("t", _dx("Breast"), BRCA2) == "unresolved"
    assert list(cache.values()) == ["unresolved"]


def test_cli_without_mixed_pieces_never_builds_a_client(fake_mongo, tmp_path, monkeypatch):
    import json

    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.build_client",
                        lambda: (_ for _ in ()).throw(AssertionError("no client needed")))
    src = tmp_path / "in.json"
    src.write_text(json.dumps([_cli_trial()]))
    llm_cli.main(["autofill-ctml", "--trials", str(src), "--out", str(tmp_path / "o.json"),
                  "--cache", str(tmp_path / "c.json")])


def test_cli_resolves_mixed_pieces_with_the_ai(fake_mongo, tmp_path, monkeypatch):
    import json

    from llm_fakes import QueuedClient

    from ctm import llm_cli

    monkeypatch.setattr(llm_cli, "load_env", lambda: None)
    monkeypatch.setattr("ctm.transformers.eligibility_to_ctml.build_client",
                        lambda: QueuedClient(['{"logic": "or"}']))
    src, out = tmp_path / "in.json", tmp_path / "o.json"
    src.write_text(json.dumps([_mixed_trial()]))
    llm_cli.main(["autofill-ctml", "--trials", str(src), "--out", str(out),
                  "--cache", str(tmp_path / "c.json"), "--yes"])
    match = json.loads(out.read_text())[0]["treatment_list"]["step"][0]["match"]
    assert {"or": [_auto(_dx("Breast")), _auto(BRCA2)]} in match
    assert json.loads((tmp_path / "c.json").read_text())


# ── mixed = same source + same text, from ANY source both stages read ────────

def test_mixed_title_piece_is_resolved_too():
    title = "Olaparib in breast cancer or in any tumor with a BRCA2 mutation"
    trial = _trial([_e("_summary.long_title", title, _dx("Breast"), "clinical", mixed=True),
                    _e("_summary.long_title", title, BRCA2, "genomic", mixed=True),
                    _e("eligibility.exclusion", "Known KRAS mutation", KRAS_NO, "genomic")])
    asked = []
    out, report = autofill_trial(trial, resolve_mixed=lambda t, c, g: asked.append(t) or "or")
    assert asked == [title]
    # resolved as an OR item even though a criterion produced a genomic node
    assert _match(out) == [{"or": [_auto(_dx("Breast")), _auto(BRCA2)]}, _auto(KRAS_NO)]
    assert report["mixed_logic"] == {"and": 0, "or": 1, "unresolved": 0}


def test_mixed_conditions_piece_and_keeps_fallback_rules():
    text = "EGFR-mutant non-small cell lung cancer"
    egfr = {"genomic": {"hugo_symbol": "EGFR", "variant_category": "Mutation", "wildtype": False}}
    trial = _trial([_e("_raw.conditions", text, _dx("Non-Small Cell Lung Cancer"), "clinical", mixed=True),
                    _e("_raw.conditions", text, egfr, "genomic", mixed=True)])
    out, report = autofill_trial(trial, resolve_mixed=lambda *a: "and")
    assert _match(out) == [_auto(_dx("Non-Small Cell Lung Cancer")), _auto(egfr)]
    assert report["mixed_logic"]["and"] == 1


def test_same_text_from_different_sources_is_not_mixed():
    text = "BRCA2"
    trial = _trial([_e("_raw.conditions", text, _dx("Breast"), "clinical"),
                    _e("_summary.long_title", text, BRCA2, "genomic")])

    def boom(*a):
        raise AssertionError("different sources are not a mixed piece")

    autofill_trial(trial, resolve_mixed=boom)


def test_report_lists_every_source_that_went_into_the_match():
    """diagnosis_sources is the diagnosis-only subset; match_sources is all of them."""
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical"),
                    _e("_raw.conditions", "Head and Neck SCC", _dx("Head and Neck Squamous Cell Carcinoma"),
                       "clinical"),
                    _e("_raw._ddots.disease_site", "Head and Neck", _dx("Head and Neck"), "clinical")],
                   nulls=[_e("eligibility.inclusion", "Signed consent", None, "clinical")])
    out, report = autofill_trial(trial)
    assert report["nodes"] == 2
    assert report["diagnosis_sources"] == ["_raw.conditions", "_raw._ddots.disease_site"]
    assert report["match_sources"] == ["_raw.conditions", "_raw._ddots.disease_site", "_raw.minimum_age"]
    assert out["_llm_curation"]["autofill"]["match_sources"] == report["match_sources"]


def test_negated_diagnosis_that_cancels_a_positive_one_is_dropped():
    """2026-11-04 neuroblastoma trial: a subgroup exclusion came back as !Neuroblastoma,
    AND-ed with the Neuroblastoma OR it matched nobody. Unrelated negations stay."""
    excl = {"and": [{"clinical": {"oncotree_primary_diagnosis": "!Neuroblastoma"}},
                    {"clinical": {"oncotree_primary_diagnosis": "!Lung"}}]}
    trial = _trial([_e("_summary.long_title", "High-Risk Neuroblastoma", _dx("Neuroblastoma"), "clinical"),
                    _e("eligibility.exclusion", "L2 without MYCN amp", excl, "clinical"),
                    _e("eligibility.exclusion", "Stage L2 only", _dx("!Neuroblastoma"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("Neuroblastoma")), _auto(_dx("!Lung"))]
    assert report["dropped_negated_diagnoses"] == ["!Neuroblastoma"]
    by_text = {e["text"]: e for e in out["_llm_curation"]["_ctml_suggestions"]}
    assert by_text["Stage L2 only"]["transferred_to_match"] is False
    assert by_text["L2 without MYCN amp"]["transferred_to_match"] is True


def _age(value):
    return {"clinical": {"age_numerical": value}}


def test_structured_age_wins_over_criteria_ages():
    """2026-11-04: age_group Children plus 'participants under 16' / 'over 16'
    criteria became <16 AND >=16 AND <18. A structured age field is the age."""
    trial = _trial([_e("_raw.age_group", "Children", _age("<18"), "clinical"),
                    _e("eligibility.inclusion", "For participants under 16", _age("<16"), "clinical"),
                    _e("eligibility.inclusion", "For participants over 16", _age(">=16"), "clinical"),
                    _e("eligibility.inclusion", "Females over 16",
                       {"clinical": {"age_numerical": ">=16", "gender": "Female"}}, "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_age("<18")), _auto({"clinical": {"gender": "Female"}})]
    assert report["age_source"] == "_raw.age_group"
    assert report["dropped_ages"] == ["<16", ">=16"]
    assert report["age_conflict"] is False
    by_text = {e["text"]: e for e in out["_llm_curation"]["_ctml_suggestions"]}
    assert by_text["For participants under 16"]["transferred_to_match"] is False
    assert by_text["Females over 16"]["transferred_to_match"] is True


def test_criteria_age_used_when_no_structured_age():
    trial = _trial([_e("_raw.age_group", "Both", None, "clinical"),
                    _e("eligibility.inclusion", "Age >= 12", _age(">=12"), "clinical"),
                    _e("eligibility.inclusion", "Age < 30", _age("<30"), "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_age(">=12")), _auto(_age("<30"))]
    assert report["age_source"] == "criteria"
    assert report["dropped_ages"] == [] and report["age_conflict"] is False


def test_contradictory_ages_are_all_dropped():
    """AND-ed ages no one can satisfy (>=18 and <18) are dropped, not written."""
    for a, b in ((">=18", "<18"), (">16", "<=16"), ("==21", ">30")):
        trial = _trial([_e("eligibility.inclusion", "A", _age(a), "clinical"),
                        _e("eligibility.inclusion", "B", _age(b), "clinical"),
                        _e("_raw.sex", "FEMALE", {"clinical": {"gender": "Female"}}, "clinical")])
        out, report = autofill_trial(trial)
        assert _match(out) == [_auto({"clinical": {"gender": "Female"}})], (a, b)
        assert report["age_conflict"] is True and report["age_source"] is None
        assert sorted(report["dropped_ages"]) == sorted([a, b])
    # Ages inside an OR are alternatives, not a conflict.
    either = {"or": [_age("<16"), _age(">=65")]}
    out, report = autofill_trial(_trial([_e("eligibility.inclusion", "Kids or seniors", either, "clinical")]))
    assert _match(out) == [_auto(either)] and report["age_conflict"] is False


def _gen(gene, category="Mutation", wildtype=False, **extra):
    return {"genomic": {"hugo_symbol": gene, "variant_category": category, "wildtype": wildtype, **extra}}


def test_contradictory_genomic_nodes_are_all_dropped():
    """2026-11-05 (pediatric LGG): one node said IDH1 mutated, another IDH1 wildtype, AND-ed
    into a match nobody can satisfy. Drop every top-level node holding a contradiction."""
    mutated = {"and": [_gen("BRAF", protein_change="p.V600E"), _gen("IDH1")]}
    wildtype = _gen("IDH1", wildtype=True)
    trial = _trial([_e("eligibility.inclusion", "LGG", _dx("Low-Grade Glioma, NOS"), "clinical"),
                    _e("eligibility.inclusion", "without BRAF/IDH1", wildtype, "genomic"),
                    _e("_summary.long_title", "BRAF V600E, IDH1", mutated, "genomic")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(_dx("Low-Grade Glioma, NOS"))]
    assert report["genomic_conflicts"] == ["IDH1 Mutation"]
    assert report["match_sources"] == ["eligibility.inclusion"]
    flags = {e["text"]: e["transferred_to_match"] for e in out["_llm_curation"]["_ctml_suggestions"]}
    assert flags == {"LGG": True, "without BRAF/IDH1": False, "BRAF V600E, IDH1": False}


def test_genes_of_interest_never_goes_into_the_match():
    """2026-11-05: _raw.octsu_genes_interest is a gene buzzword list, not a requirement.
    Its node (from trials drafted before the genomic stage nulled it) is ignored."""
    braf = _gen("BRAF", protein_change="p.V600E")
    trial = _trial([_e("_raw.minimum_age", "18 Years", AGE, "clinical"),
                    _e("_raw.octsu_genes_interest", "BRAF V600E", braf, "genomic")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(AGE)] and report["match_sources"] == ["_raw.minimum_age"]
    genes = [e for e in out["_llm_curation"]["_ctml_suggestions"] if e["source"] == "_raw.octsu_genes_interest"]
    assert genes[0]["transferred_to_match"] is False


def test_mutated_and_no_documented_mutation_conflict_too():
    trial = _trial([_e("eligibility.inclusion", "A", _gen("KRAS"), "genomic"),
                    _e("eligibility.exclusion", "B", KRAS_NO, "genomic"),
                    _e("_raw.minimum_age", "18 Years", AGE, "clinical")])
    out, report = autofill_trial(trial)
    assert _match(out) == [_auto(AGE)] and report["genomic_conflicts"] == ["KRAS Mutation"]


def test_compatible_genomic_nodes_are_kept():
    """Different protein changes, wildtype vs no documented mutation, and alternatives
    under an OR are not contradictions."""
    for a, b in ((_gen("BRAF", protein_change="p.V600E"), _gen("BRAF", protein_change="p.V600K")),
                 (_gen("KRAS", wildtype=True), KRAS_NO),
                 ({"or": [_gen("EGFR"), _gen("ALK", "Structural Variation")]}, _gen("EGFR", wildtype=True))):
        out, report = autofill_trial(_trial([_e("eligibility.inclusion", "A", a, "genomic"),
                                             _e("eligibility.inclusion", "B", b, "genomic")]))
        assert _match(out) == [_auto(a), _auto(b)] and report["genomic_conflicts"] == []
