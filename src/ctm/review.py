"""Pure functions behind ``ctm-review``: change one trial's match leaves, build its log.

A leaf is addressed by ``step_index`` plus a path inside that step's match, e.g.
``match[0].and[2].or[1]``. Every path refers to the trial **as loaded**: all paths are
resolved before anything changes, verify/edit replace in place, adds append to the end
of their parent list, and deletes are removed last. No action can shift the position
another one points to, so the page can stage actions in any order and the log
records positions a reader can find in the trial they came from.

No I/O, and inputs are never mutated.
"""
import copy
import json
import re
from collections.abc import Iterator

from ctm.trials_lifecycle import trial_key

MARKER = "_CURATED_METHOD"
VERIFIED = "verified"
MANUAL = "manual"
ACTIONS = ("verified", "edited", "deleted", "added")
LEAF_KINDS = ("clinical", "genomic")
WRAPPERS = ("and", "or")

_SEGMENT = re.compile(r"([A-Za-z_]+)((?:\[\d+\])*)$")
_DELETED = object()   # placeholder for a deleted leaf until the tree is pruned


# ── paths and node shapes ────────────────────────────────────────────────────

def parse_path(path: str) -> list[str | int]:
    """``"match[0].and[2]"`` → ``["match", 0, "and", 2]``."""
    segments: list[str | int] = []
    for part in str(path).split("."):
        m = _SEGMENT.match(part)
        if not m:
            raise ValueError(f"bad path {path!r}")
        segments.append(m.group(1))
        segments.extend(int(i) for i in re.findall(r"\[(\d+)\]", m.group(2)))
    if not segments or segments[0] != "match":
        raise ValueError(f"path must start with 'match': {path!r}")
    return segments


def format_path(segments: list[str | int]) -> str:
    out = ""
    for seg in segments:
        out += f"[{seg}]" if isinstance(seg, int) else (f".{seg}" if out else seg)
    return out


def is_leaf(node) -> bool:
    return (isinstance(node, dict) and len(node) == 1
            and next(iter(node)) in LEAF_KINDS and isinstance(next(iter(node.values())), dict))


def _is_wrapper(node) -> bool:
    return (isinstance(node, dict) and len(node) == 1
            and next(iter(node)) in WRAPPERS and isinstance(next(iter(node.values())), list))


def iter_leaves(match: list) -> Iterator[tuple[str, dict]]:
    """Every leaf in a step's match list, depth-first, as ``(path, leaf)``."""
    def walk(items, prefix):
        for i, item in enumerate(items):
            path = [*prefix, i]
            if is_leaf(item):
                yield format_path(path), item
            elif _is_wrapper(item):
                key = next(iter(item))
                yield from walk(item[key], [*path, key])
    yield from walk(match or [], ["match"])


def without_marker(leaf: dict) -> dict:
    return {kind: {k: v for k, v in fields.items() if k != MARKER}
            for kind, fields in leaf.items()}


def _with_marker(leaf: dict, value: str) -> dict:
    return {kind: {**copy.deepcopy(fields), MARKER: value} for kind, fields in leaf.items()}


def validate_node(value: str | dict) -> dict:
    """A user-typed leaf, checked and marked ``manual``. Raises ``ValueError``."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from None
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("a leaf is one object with exactly one key, 'clinical' or 'genomic'")
    kind, fields = next(iter(value.items()))
    if kind not in LEAF_KINDS:
        raise ValueError(f"the key must be 'clinical' or 'genomic', not {kind!r}")
    if not isinstance(fields, dict) or not {k for k in fields if k != MARKER}:
        raise ValueError(f"'{kind}' must be a non-empty object")
    return _with_marker(value, MANUAL)


# ── applying staged actions ──────────────────────────────────────────────────

def _walk(step: dict, segments: list[str | int]):
    obj = step
    try:
        for seg in segments:
            if isinstance(seg, int) and not isinstance(obj, list):
                raise TypeError
            obj = obj[seg]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"no such position {format_path(segments)!r}") from None
    return obj


def _prune(items: list) -> list:
    """Drop deleted leaves, then any wrapper that a delete left empty."""
    out = []
    for item in items:
        if item is _DELETED:
            continue
        if _is_wrapper(item):
            key = next(iter(item))
            kids = _prune(item[key])
            if not kids and item[key]:
                continue
            item = {key: kids}
        out.append(item)
    return out


def apply_actions(treatment_list: dict, actions: list[dict]) -> tuple[dict, list[dict]]:
    """Apply staged actions to a copy of ``treatment_list``.

    Returns ``(new_treatment_list, resolved)``; each resolved item is the action plus
    ``node_before`` and ``node_after``. Raises ``ValueError`` on any bad action, before
    returning anything, so a batch is all-or-nothing.
    """
    new = copy.deepcopy(treatment_list)
    steps = new.get("step") or []
    plans, targeted = [], set()

    for action in actions:
        kind = action.get("action")
        if kind not in ACTIONS:
            raise ValueError(f"unknown action {kind!r}")
        idx = action.get("step_index")
        if not isinstance(idx, int) or not 0 <= idx < len(steps):
            raise ValueError(f"no step {idx!r}")
        segments = parse_path(action.get("path"))
        path = format_path(segments)
        step = steps[idx]

        if kind == "added":
            target = _walk(step, segments)
            if segments == ["match"] and isinstance(target, list):
                children = target
            elif _is_wrapper(target):
                children = target[next(iter(target))]
            else:
                raise ValueError(f"can only add under 'match' or an and/or, not {path!r}")
            if "node" not in action:
                raise ValueError(f"added at {path!r} has no node")
            plans.append((action, children, None, None, validate_node(action["node"])))
            continue

        if not isinstance(segments[-1], int):
            raise ValueError(f"{path!r} is not a leaf")
        parent = _walk(step, segments[:-1])
        leaf = _walk(step, segments)
        if not isinstance(parent, list) or not is_leaf(leaf):
            raise ValueError(f"{path!r} is not a leaf")
        if (idx, path) in targeted:
            raise ValueError(f"two actions on {path!r} in step {idx}")
        targeted.add((idx, path))

        if kind == "verified":
            after = _with_marker(leaf, VERIFIED)
        elif kind == "edited":
            if "node" not in action:
                raise ValueError(f"edited at {path!r} has no node")
            after = validate_node(action["node"])
        else:
            after = None
        plans.append((action, parent, segments[-1], copy.deepcopy(leaf), after))

    resolved, pruned_steps = [], set()
    for action, container, index, before, after in plans:
        if action["action"] == "added":
            container.append(after)
        elif action["action"] == "deleted":
            container[index] = _DELETED
            pruned_steps.add(action["step_index"])
        else:
            container[index] = after
        resolved.append({**action, "path": format_path(parse_path(action["path"])),
                         "node_before": before, "node_after": copy.deepcopy(after)})

    for idx in pruned_steps:
        steps[idx]["match"] = _prune(steps[idx]["match"])
    return new, resolved


# ── source link and change-log documents ─────────────────────────────────────

def _leaves_in(node) -> Iterator[dict]:
    """Every leaf in a suggestion's ``suggested_node`` (a leaf or an and/or tree)."""
    if is_leaf(node):
        yield node
    elif _is_wrapper(node):
        for child in next(iter(node.values())):
            yield from _leaves_in(child)


def link_source(leaf: dict, curation: dict | None) -> tuple[str | None, str | None]:
    """``(source, text)`` of the first AI suggestion holding this leaf, else ``(None, None)``.

    Copied into the log because a later ``ctm-llm`` rerun replaces the suggestions,
    and with them the text the leaf was drafted from.
    """
    want = without_marker(leaf)
    for entry in (curation or {}).get("_ctml_suggestions") or []:
        if any(without_marker(cand) == want for cand in _leaves_in(entry.get("suggested_node"))):
            return entry.get("source"), entry.get("text")
    return None, None


def build_review_docs(trial: dict, resolved: list[dict], *, reviewer: str, reviewed_at,
                      batch_id: str, processed_with: str) -> list[dict]:
    """One ``curation_reviews`` document per applied action."""
    curation = trial.get("_llm_curation") or {}
    docs = []
    for r in resolved:
        before = r.get("node_before")
        source, text = link_source(before, curation) if before else (None, None)
        docs.append({
            "action": r["action"],
            "entity": trial.get("entity"),
            "trial_key": trial_key(trial),
            "nct_id": trial.get("nct_id"),
            "protocol_no": trial.get("protocol_no"),
            "step_index": r["step_index"],
            "path": r["path"],
            "node_before": copy.deepcopy(before),
            "node_after": copy.deepcopy(r.get("node_after")),
            "note": r.get("note") or "",
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "batch_id": batch_id,
            "trial_hash": trial.get("trial_hash"),
            "source_field": source,
            "source_text": text,
            "stage_runs": copy.deepcopy(curation.get("stage_runs") or {}),
            "processed_with": processed_with,
        })
    return docs
