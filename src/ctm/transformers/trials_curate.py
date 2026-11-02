"""Helpers left over from the pre-2.4.0 biomarker stage.

The biomarker scan itself became ``ctm-llm genomic`` (see ``genomic_ctml``),
which writes ``_llm_curation.molecular_genomic_refs`` per piece of text.
What stays here is shared: trial IDs, JSON-array parsing (``confidence_split``),
JSON caches and the known-gene list behind ``in_kb``.
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _parse_json_array(raw: str) -> list:
    """Strip markdown code fences if present, then parse a JSON array.

    Models sometimes wrap output in ```json ... ``` despite being told not to.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


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
