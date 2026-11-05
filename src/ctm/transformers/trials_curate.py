"""Helpers left over from the pre-2.4.0 biomarker stage.

The biomarker scan itself became ``ctm-llm genomic`` (see ``genomic_ctml``),
which writes ``_llm_curation.molecular_genomic_refs`` per piece of text.
What stays here is shared: trial IDs, JSON caches and the known-gene list behind ``in_kb``.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _trial_id(trial: dict) -> str:
    return trial.get("nct_id") or trial.get("protocol_no") or "unknown"


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def load_known_genes(kb_path: Path) -> set[str]:
    kb = json.loads(kb_path.read_text())
    return {g["name"].upper() for g in kb}
