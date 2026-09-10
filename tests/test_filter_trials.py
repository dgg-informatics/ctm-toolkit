"""Tests for filter_trials.py — deriving one deduplicated trial set from the master."""


def _row(entity, nct=None, protocol=None, inclusion=("Age >= 18",), title="A study",
         trial_hash=None, match=None):
    return {
        "entity": entity, "nct_id": nct, "protocol_no": protocol,
        "trial_hash": trial_hash or ((entity[0] * 8 + "0" * 56)[:64]),
        "status": "open to accrual",
        "eligibility": {"inclusion": [{"text": t, "sub_criteria": []} for t in inclusion],
                        "exclusion": []},
        "treatment_list": {"step": [{"match": match or []}]},
        "_summary": {"short_title": title, "status": [{"value": "open to accrual"}]},
        "_raw": {"amc_id": "871"},
    }


# ── normalization and fingerprinting ────────────────────────────────────────

def test_normalize_collapses_whitespace():
    from ctm.transformers.filter_trials import normalize_criterion_text
    assert normalize_criterion_text("  Age   >=\n18  ") == "Age >= 18"


def test_normalize_folds_unicode_punctuation():
    from ctm.transformers.filter_trials import normalize_criterion_text
    # Input with curly quotes (U+201C, U+201D) and en dashes (U+2013)
    input_str = "“ECOG” – 0–2"
    expected = '"ECOG" - 0-2'
    assert normalize_criterion_text(input_str) == expected


def test_normalize_preserves_case_and_punctuation():
    """Lowercasing or stripping punctuation could hide a real clinical difference."""
    from ctm.transformers.filter_trials import normalize_criterion_text
    assert normalize_criterion_text("ECOG 0-2.") == "ECOG 0-2."
    assert normalize_criterion_text("ecog 0-2.") != normalize_criterion_text("ECOG 0-2.")


def test_criterion_hash_separates_inclusion_from_exclusion():
    """Identical text means the opposite thing in each section."""
    from ctm.transformers.filter_trials import criterion_hash
    c = {"text": "Prior chemotherapy", "sub_criteria": []}
    assert criterion_hash(c, "inclusion") != criterion_hash(c, "exclusion")


def test_criterion_hash_includes_sub_criteria():
    from ctm.transformers.filter_trials import criterion_hash
    a = {"text": "Notes:", "sub_criteria": [{"text": "IGCCC applies", "sub_criteria": []}]}
    b = {"text": "Notes:", "sub_criteria": []}
    assert criterion_hash(a, "inclusion") != criterion_hash(b, "inclusion")


def test_fingerprint_is_order_insensitive():
    from ctm.transformers.filter_trials import eligibility_fingerprint
    a = {"inclusion": [{"text": "A", "sub_criteria": []}, {"text": "B", "sub_criteria": []}],
         "exclusion": []}
    b = {"inclusion": [{"text": "B", "sub_criteria": []}, {"text": "A", "sub_criteria": []}],
         "exclusion": []}
    assert eligibility_fingerprint(a) == eligibility_fingerprint(b)


def test_fingerprint_changes_when_a_criterion_changes():
    from ctm.transformers.filter_trials import eligibility_fingerprint
    a = {"inclusion": [{"text": "ECOG 0-2", "sub_criteria": []}], "exclusion": []}
    b = {"inclusion": [{"text": "ECOG 0-1", "sub_criteria": []}], "exclusion": []}
    assert eligibility_fingerprint(a) != eligibility_fingerprint(b)


def test_fingerprint_handles_missing_eligibility():
    from ctm.transformers.filter_trials import eligibility_fingerprint
    assert eligibility_fingerprint(None) == eligibility_fingerprint({})


# ── grouping ────────────────────────────────────────────────────────────────

def test_group_key_prefers_nct_id():
    from ctm.transformers.filter_trials import group_key
    assert group_key(_row("amc", nct="NCT1", protocol="2017.130")) == "NCT1"


def test_group_key_falls_back_to_protocol_no():
    """Measured: exactly 1 AMC trial on the master has no NCT."""
    from ctm.transformers.filter_trials import group_key
    assert group_key(_row("amc", nct=None, protocol="2017.130")) == "2017.130"


def test_group_key_raises_when_both_missing():
    import pytest
    from ctm.transformers.filter_trials import group_key
    with pytest.raises(ValueError, match="neither nct_id nor protocol_no"):
        group_key(_row("amc", nct=None, protocol=None))


# ── entity precedence ───────────────────────────────────────────────────────

def test_winning_entity_prefers_amc():
    from ctm.transformers.filter_trials import winning_entity
    assert winning_entity([_row("west", nct="NCT1"), _row("sparrow-api", nct="NCT1"),
                           _row("amc", nct="NCT1")]) == "amc"


def test_winning_entity_prefers_sparrow_over_west():
    from ctm.transformers.filter_trials import winning_entity
    assert winning_entity([_row("west", nct="NCT1"),
                           _row("sparrow-api", nct="NCT1")]) == "sparrow-api"


def test_winning_entity_keeps_a_lone_west():
    from ctm.transformers.filter_trials import winning_entity
    assert winning_entity([_row("west", nct="NCT1")]) == "west"


def test_winning_entity_is_deterministic_for_unknown_entities():
    """An entity added upstream without being added to ENTITY_PRECEDENCE must not
    depend on input order."""
    from ctm.transformers.filter_trials import winning_entity
    rows = [_row("zeta", nct="NCT1"), _row("alpha", nct="NCT1")]
    assert winning_entity(rows) == "alpha"
    assert winning_entity(list(reversed(rows))) == "alpha"


def test_precedence_drops_lower_entities():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1"), _row("amc", nct="NCT1")])
    assert len(out) == 1
    assert out[0]["entity"] == "amc"


# ── eligibility collapse ────────────────────────────────────────────────────

def test_identical_eligibility_collapses_to_lowest_trial_hash():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1", trial_hash="b" * 64),
                         _row("west", nct="NCT1", trial_hash="a" * 64)])
    assert len(out) == 1
    assert out[0]["trial_hash"] == "a" * 64


def test_whitespace_only_difference_still_collapses():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1", inclusion=("Age  >=  18",), trial_hash="a" * 64),
                         _row("west", nct="NCT1", inclusion=("Age >= 18",), trial_hash="b" * 64)])
    assert len(out) == 1


def test_reordered_criteria_still_collapse():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1", inclusion=("A", "B"), trial_hash="a" * 64),
                         _row("west", nct="NCT1", inclusion=("B", "A"), trial_hash="b" * 64)])
    assert len(out) == 1


def test_differing_eligibility_keeps_both():
    """NCT02445222: two AMC protocols sharing an NCT are distinct studies."""
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("amc", nct="NCT1", protocol="2015.001", inclusion=("Age >= 18",),
                              trial_hash="a" * 64),
                         _row("amc", nct="NCT1", protocol="2015.063", inclusion=("Age >= 65",),
                              trial_hash="b" * 64)])
    assert len(out) == 2
    assert sorted(t["protocol_no"] for t in out) == ["2015.001", "2015.063"]


def test_treatment_list_is_excluded_from_equality():
    """Hand-curated, so it can differ between copies of one study."""
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([
        _row("west", nct="NCT1", trial_hash="a" * 64, match=[{"clinical": {"age_numerical": ">=18"}}]),
        _row("west", nct="NCT1", trial_hash="b" * 64, match=[])])
    assert len(out) == 1


def test_short_title_is_excluded_from_equality():
    """AMC edits titles, so title differences must not preserve duplicates."""
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1", title="Study A", trial_hash="a" * 64),
                         _row("west", nct="NCT1", title="Study A (rev 2)", trial_hash="b" * 64)])
    assert len(out) == 1


# ── entities multiset ───────────────────────────────────────────────────────

def test_entities_retains_duplicates_and_is_sorted():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("sparrow-api", nct="NCT1", trial_hash="s" * 64),
                         _row("amc", nct="NCT1", inclusion=("A",), trial_hash="a" * 64),
                         _row("amc", nct="NCT1", inclusion=("B",), trial_hash="b" * 64)])
    assert all(t["entities"] == ["amc", "amc", "sparrow-api"] for t in out)


def test_entities_present_on_sole_row_trial():
    from ctm.transformers.filter_trials import filter_trials
    assert filter_trials([_row("amc", nct="NCT1")])[0]["entities"] == ["amc"]


# ── filtered_reason ─────────────────────────────────────────────────────────

def test_reason_unique_nct():
    from ctm.transformers.filter_trials import filter_trials
    assert filter_trials([_row("amc", nct="NCT1")])[0]["filtered_reason"] == "unique-nct"


def test_reason_entity_precedence():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("amc", nct="NCT1"), _row("west", nct="NCT1")])
    assert out[0]["filtered_reason"] == "entity-precedence"


def test_reason_same_nct_unique_eligibility():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("amc", nct="NCT1", inclusion=("A",), trial_hash="a" * 64),
                         _row("amc", nct="NCT1", inclusion=("B",), trial_hash="b" * 64)])
    assert {t["filtered_reason"] for t in out} == {"same-nct-unique-eligibility"}


def test_reason_same_nct_duplicate_eligibility():
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("west", nct="NCT1", trial_hash="a" * 64),
                         _row("west", nct="NCT1", trial_hash="b" * 64)])
    assert out[0]["filtered_reason"] == "same-nct-duplicate-eligibility"


def test_reason_is_per_document_when_mixed():
    """Three winning-entity rows: two identical, one distinct. Each survivor gets
    the reason that actually applies to it."""
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([_row("amc", nct="NCT1", inclusion=("A",), trial_hash="a" * 64),
                         _row("amc", nct="NCT1", inclusion=("A",), trial_hash="b" * 64),
                         _row("amc", nct="NCT1", inclusion=("Z",), trial_hash="c" * 64)])
    assert len(out) == 2
    by_hash = {t["trial_hash"]: t["filtered_reason"] for t in out}
    assert by_hash["a" * 64] == "same-nct-duplicate-eligibility"
    assert by_hash["c" * 64] == "same-nct-unique-eligibility"


# ── real observed shapes ────────────────────────────────────────────────────

def test_nct06580314_shape_amc_wins_over_identical_others():
    """1 amc (unique title) + 2 identical sparrow-api + 1 west → amc only."""
    from ctm.transformers.filter_trials import filter_trials
    out = filter_trials([
        _row("amc", nct="NCT06580314", title="AMC title", inclusion=("A",), trial_hash="a" * 64),
        _row("sparrow-api", nct="NCT06580314", inclusion=("B",), trial_hash="s" * 64),
        _row("sparrow-api", nct="NCT06580314", inclusion=("B",), trial_hash="t" * 64),
        _row("west", nct="NCT06580314", inclusion=("B",), trial_hash="w" * 64)])
    assert len(out) == 1
    assert out[0]["entity"] == "amc"
    assert out[0]["entities"] == ["amc", "sparrow-api", "sparrow-api", "west"]
    assert out[0]["filtered_reason"] == "entity-precedence"


# ── passthrough and determinism ─────────────────────────────────────────────

def test_winning_document_passes_through_unchanged():
    """Only entities and filtered_reason may be added; no field may be rewritten."""
    from ctm.transformers.filter_trials import filter_trials
    source = _row("amc", nct="NCT1", protocol="2017.130")
    out = filter_trials([source])[0]
    assert set(out) - set(source) == {"entities", "filtered_reason"}
    for key, value in source.items():
        assert out[key] == value


def test_shuffled_input_produces_identical_output():
    import random
    from ctm.transformers.filter_trials import filter_trials
    rows = [_row("west", nct="NCT2", trial_hash="w" * 64),
            _row("amc", nct="NCT1", inclusion=("A",), trial_hash="a" * 64),
            _row("amc", nct="NCT1", inclusion=("B",), trial_hash="b" * 64),
            _row("sparrow-api", nct="NCT3", trial_hash="s" * 64)]
    baseline = filter_trials(rows)
    for seed in range(5):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert filter_trials(shuffled) == baseline


def test_empty_input_returns_empty():
    from ctm.transformers.filter_trials import filter_trials
    assert filter_trials([]) == []
