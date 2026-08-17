"""Tests for src/ctm/db.py — config resolution and document stamping.

Everything here runs without a live MongoDB: mongo_config() only reads the
environment, and stamp()/strip_metadata() are pure functions over dicts.
"""
import pytest


@pytest.fixture(autouse=True)
def _clean_mongo_env(monkeypatch):
    for name in (
        "MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME",
        "MONGO_MASTER_DBNAME", "MONGO_MASTER_COLLECTION",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_required(monkeypatch):
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27018")
    monkeypatch.setenv("MONGO_DBNAME", "2026-08-17_dev")


@pytest.mark.parametrize("missing", ["MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME"])
def test_mongo_config_names_the_missing_variable(monkeypatch, missing):
    """Fails fast by name, matching build_client()'s UMGPT_API_KEY style."""
    from ctm.db import mongo_config

    _set_required(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        mongo_config()


def test_mongo_config_rejects_a_non_integer_port(monkeypatch):
    from ctm.db import mongo_config

    _set_required(monkeypatch)
    monkeypatch.setenv("MONGO_PORT", "not-a-port")

    with pytest.raises(ValueError, match="MONGO_PORT"):
        mongo_config()


def test_mongo_config_defaults_the_master_collection(monkeypatch):
    from ctm.db import DEFAULT_MASTER_COLLECTION, mongo_config

    _set_required(monkeypatch)
    assert mongo_config()["master_collection"] == DEFAULT_MASTER_COLLECTION
    assert DEFAULT_MASTER_COLLECTION == "06_master_trials"


def test_mongo_config_honours_an_explicit_master_collection(monkeypatch):
    from ctm.db import mongo_config

    _set_required(monkeypatch)
    monkeypatch.setenv("MONGO_MASTER_COLLECTION", "trials")
    assert mongo_config()["master_collection"] == "trials"


def test_mongo_config_requires_master_dbname_only_when_asked(monkeypatch):
    """A --master <file> run must not fail on a variable it never reads."""
    from ctm.db import mongo_config

    _set_required(monkeypatch)

    assert mongo_config()["master_dbname"] is None

    with pytest.raises(ValueError, match="MONGO_MASTER_DBNAME"):
        mongo_config(require_master=True)


def test_mongo_config_master_dbname_is_not_the_run_database(monkeypatch):
    """The master is deliberately outside the per-run DB — see db.py's docstring."""
    from ctm.db import mongo_config

    _set_required(monkeypatch)
    monkeypatch.setenv("MONGO_MASTER_DBNAME", "ctm_master")

    config = mongo_config(require_master=True)
    assert config["master_dbname"] == "ctm_master"
    assert config["dbname"] == "2026-08-17_dev"
    assert config["master_dbname"] != config["dbname"]


def _amc_trial(**extra):
    return {
        "entity": "amc",
        "protocol_no": "2021.070",
        "nct_id": None,
        "eligibility": {"inclusion": [], "exclusion": []},
        "treatment_list": {"step": []},
        "_raw": {"status": "open"},
        **extra,
    }


def test_stamp_reads_the_version_from_package_metadata(monkeypatch):
    """Not a hardcoded string — it must follow the installed version."""
    from ctm import db as ctm_db

    monkeypatch.setattr(ctm_db, "toolkit_version", lambda: "9.9.9")
    stamped = ctm_db.stamp(_amc_trial(), "ctm-mm trials-diff", "2026-08-17")

    assert stamped["processed_with"] == "ctm-mm trials-diff 9.9.9"


def test_toolkit_version_matches_installed_metadata():
    from importlib.metadata import version

    from ctm.db import toolkit_version

    assert toolkit_version() == version("ctm-toolkit")


def test_stamp_sets_run_date_and_extra_fields():
    from ctm.db import stamp

    stamped = stamp(_amc_trial(), "ctm-mm trials-diff", "2026-08-17", diff_status="unchanged")

    assert stamped["run_date"] == "2026-08-17"
    assert stamped["diff_status"] == "unchanged"


def test_stamp_derives_trial_key_from_protocol_no_for_amc():
    from ctm.db import stamp

    stamped = stamp(_amc_trial(), "ctm-mm trials-diff", "2026-08-17")
    assert stamped["trial_key"] == "2021.070"


def test_stamp_derives_trial_key_from_nct_id_for_non_amc():
    from ctm.db import stamp

    trial = {"entity": "west", "protocol_no": None, "nct_id": "NCT04858334"}
    stamped = stamp(trial, "ctm-mm trials-diff", "2026-08-17")
    assert stamped["trial_key"] == "NCT04858334"


def test_stamp_strips_inherited_metadata_rather_than_carrying_it_forward():
    """`unchanged` and `deleted` copy master fields forward — an inherited _id
    would collide on the next write and leak into the JSON files."""
    from ctm.db import stamp

    from_mongo = _amc_trial(
        _id="deadbeefdeadbeefdeadbeef",
        processed_with="ctm-mm trials-diff 0.0.1",
        run_date="2026-08-10",
        diff_status="changed",
        trial_key="stale-key",
    )
    stamped = stamp(from_mongo, "ctm-mm trials-diff", "2026-08-17", diff_status="unchanged")

    assert "_id" not in stamped
    assert stamped["processed_with"] != "ctm-mm trials-diff 0.0.1"
    assert stamped["run_date"] == "2026-08-17"
    assert stamped["diff_status"] == "unchanged"
    assert stamped["trial_key"] == "2021.070"


def test_stamp_preserves_an_existing_trial_hash():
    from ctm.db import stamp

    stamped = stamp(_amc_trial(trial_hash="c" * 64), "ctm-mm trials-diff", "2026-08-17")
    assert stamped["trial_hash"] == "c" * 64


def test_stamp_recomputes_a_missing_trial_hash():
    """A master predating trial_hash stamping (or a hand-trimmed one) still has to
    be storable — trial_hash is the unique key. compute_trial_hash() is pure over
    _raw, so this reproduces what the upstream stage would have written."""
    from ctm.db import stamp
    from ctm.trials_lifecycle import compute_trial_hash

    trial = _amc_trial()
    assert "trial_hash" not in trial

    stamped = stamp(trial, "ctm-mm trials-diff", "2026-08-17")
    assert stamped["trial_hash"] == compute_trial_hash(trial)
    assert len(stamped["trial_hash"]) == 64


def test_stamp_recomputed_hashes_distinguish_different_raw_blobs():
    """The recomputed value must still be a usable unique key."""
    from ctm.db import stamp

    a = stamp({"entity": "west", "nct_id": "NCT1", "_raw": {"x": 1}}, "s", "2026-08-17")
    b = stamp({"entity": "west", "nct_id": "NCT1", "_raw": {"x": 2}}, "s", "2026-08-17")
    assert a["trial_hash"] != b["trial_hash"]


def test_stamp_does_not_mutate_its_input():
    from ctm.db import stamp

    trial = _amc_trial()
    before = dict(trial)
    stamp(trial, "ctm-mm trials-diff", "2026-08-17", diff_status="changed")
    assert trial == before


class _FakeCollection:
    def __init__(self):
        self.dropped = False
        self.indexes = []
        self.inserted = None
        self.ordered = None

    def drop(self):
        self.dropped = True

    def create_index(self, keys, unique=False):
        self.indexes.append({"keys": keys, "unique": unique})

    def insert_many(self, docs, ordered=True):
        self.inserted = docs
        self.ordered = ordered


class _FakeDb:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, name):
        return self._collection


def test_replace_collection_drops_then_indexes_then_inserts():
    """Dropping, not delete_many: delete_many leaves stale indexes behind, which
    keep enforcing a previous run's key definition."""
    from ctm.db import DIFF_LOOKUP_KEYS, DIFF_UNIQUE_KEY, replace_collection

    collection = _FakeCollection()
    docs = [{"trial_hash": "a" * 64, "entity": "amc", "trial_key": "2021.070"}]

    replace_collection(_FakeDb(collection), "02_diff_trials", docs,
                       DIFF_UNIQUE_KEY, DIFF_LOOKUP_KEYS)

    assert collection.dropped
    assert collection.indexes == [
        {"keys": "trial_hash", "unique": True},
        {"keys": [("entity", 1), ("trial_key", 1)], "unique": False},
    ]
    assert collection.inserted == docs


def test_replace_collection_inserts_unordered():
    """ordered=False: one rejected document must not truncate the tail of the
    batch — `deleted` documents are stamped last — and the error should name
    every offender at once rather than one per round trip."""
    from ctm.db import DIFF_UNIQUE_KEY, replace_collection

    collection = _FakeCollection()
    replace_collection(_FakeDb(collection), "02_diff_trials",
                       [{"trial_hash": "a" * 64}], DIFF_UNIQUE_KEY)

    assert collection.ordered is False


def test_replace_collection_refuses_unkeyed_documents_without_dropping():
    """Validating before the drop keeps the previous run's data intact."""
    from ctm.db import DIFF_UNIQUE_KEY, replace_collection

    collection = _FakeCollection()
    docs = [{"trial_hash": "a" * 64}, {"entity": "amc"}]  # second has no trial_hash

    with pytest.raises(ValueError, match="trial_hash"):
        replace_collection(_FakeDb(collection), "02_diff_trials", docs, DIFF_UNIQUE_KEY)

    assert not collection.dropped
    assert collection.inserted is None


def test_prepare_collection_refuses_a_collection_no_stage_owns():
    """05_manual_curated_trials holds days of a curator's hand edits. A stage that
    dropped it would discard them with no warning and no recovery."""
    from ctm.db import DIFF_UNIQUE_KEY, MANUAL_COLLECTION, prepare_collection

    collection = _FakeCollection()

    with pytest.raises(ValueError, match="not a machine-written collection"):
        prepare_collection(_FakeDb(collection), MANUAL_COLLECTION, DIFF_UNIQUE_KEY)

    assert not collection.dropped


def test_manual_collection_is_excluded_from_machine_written():
    from ctm import db as ctm_db

    assert ctm_db.MANUAL_COLLECTION not in ctm_db.MACHINE_WRITTEN
    for owned in (ctm_db.NORMALIZED_COLLECTION, ctm_db.DIFF_COLLECTION,
                  ctm_db.CTML_COLLECTION, ctm_db.CURATED_COLLECTION):
        assert owned in ctm_db.MACHINE_WRITTEN


def test_collection_map_is_ordered_by_pipeline_stage():
    """Ordinal prefixes are the reason renames cascade; keep the map honest."""
    from ctm import db as ctm_db

    names = [
        ctm_db.NORMALIZED_COLLECTION, ctm_db.DIFF_COLLECTION, ctm_db.CTML_COLLECTION,
        ctm_db.CURATED_COLLECTION, ctm_db.MANUAL_COLLECTION,
        ctm_db.DEFAULT_MASTER_COLLECTION,
    ]
    assert names == sorted(names), "prefixes must sort into pipeline order"
    assert [n.split("_")[0] for n in names] == ["01", "02", "03", "04", "05", "06"]


def test_upsert_doc_replaces_on_the_unique_key():
    from ctm.db import DIFF_UNIQUE_KEY, upsert_doc

    class _Recorder:
        def __init__(self):
            self.calls = []

        def replace_one(self, filt, doc, upsert=False):
            self.calls.append((filt, doc, upsert))

    collection = _Recorder()
    upsert_doc(collection, {"trial_hash": "a" * 64, "x": 1}, DIFF_UNIQUE_KEY)

    filt, doc, upsert = collection.calls[0]
    assert filt == {"trial_hash": "a" * 64}
    assert upsert is True
    assert doc["x"] == 1


def test_upsert_doc_refuses_an_unkeyed_document():
    from ctm.db import DIFF_UNIQUE_KEY, upsert_doc

    with pytest.raises(ValueError, match="trial_hash"):
        upsert_doc(object(), {"x": 1}, DIFF_UNIQUE_KEY)


def test_inherited_run_date_takes_the_value_from_source_documents():
    """A stage must not read the clock: the weekly cycle spans days, so
    date.today() at each stage splits one run across several run_dates."""
    from ctm.db import inherited_run_date

    docs = [{"run_date": "2026-08-17"}, {"run_date": "2026-08-17"}]
    assert inherited_run_date(docs) == "2026-08-17"
    # Even when "today" is something else entirely.
    assert inherited_run_date(docs, fallback="2026-09-01") == "2026-08-17"


def test_inherited_run_date_rejects_mixed_runs():
    from ctm.db import inherited_run_date

    with pytest.raises(ValueError, match="multiple run_dates"):
        inherited_run_date([{"run_date": "2026-08-17"}, {"run_date": "2026-08-24"}])


def test_inherited_run_date_falls_back_when_sources_carry_none():
    from ctm.db import inherited_run_date

    assert inherited_run_date([{"x": 1}], fallback="2026-08-17") == "2026-08-17"

    with pytest.raises(ValueError, match="no run_date"):
        inherited_run_date([{"x": 1}])


def test_replace_collection_handles_an_empty_batch():
    from ctm.db import DIFF_UNIQUE_KEY, replace_collection

    collection = _FakeCollection()
    replace_collection(_FakeDb(collection), "02_diff_trials", [], DIFF_UNIQUE_KEY)

    assert collection.dropped
    assert collection.inserted is None


def test_strip_metadata_keeps_trial_hash():
    """trial_hash is real trial data stamped by `ctm-mm trials`, not storage metadata."""
    from ctm.db import strip_metadata

    trial = _amc_trial(trial_hash="a" * 64, _id="deadbeef")
    stripped = strip_metadata(trial)

    assert stripped["trial_hash"] == "a" * 64
    assert "_id" not in stripped
