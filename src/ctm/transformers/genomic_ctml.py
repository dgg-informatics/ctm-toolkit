"""``ctm-llm genomic`` — genomic CTML suggestions and molecular refs for one trial.

Validation pins every genomic node to the **trial-side** vocabulary matchengine
maps from (the user's key table, 02Nov2026). A wrong ``mmr_status`` /
``ms_status`` value crashes matchengine, and a wrong ``variant_category`` or a
string ``wildtype`` silently matches nothing, so nothing the model writes reaches
a curator unchecked.
"""
import logging

log = logging.getLogger(__name__)

VARIANT_CATEGORIES = frozenset({"Mutation", "Copy Number Variation", "Structural Variation"})
CNV_CALLS = frozenset({"High Amplification", "Low Amplification",
                       "Homozygous Deletion", "Heterozygous Deletion"})
MMR_VALUES = frozenset({"MMR-Deficient", "MMR-Proficient"})
MS_VALUES = frozenset({"MSI-H", "MSI-L", "MSS"})
VARIANT_CLASSIFICATIONS = frozenset({"Missense_Mutation", "Nonsense_Mutation", "In_Frame_Del",
                                     "Frame_Shift_Del", "Splice_Site"})
GENOMIC_KEYS = frozenset({"hugo_symbol", "variant_category", "protein_change",
                          "wildcard_protein_change", "wildtype", "cnv_call", "mmr_status",
                          "ms_status", "variant_classification", "exon"})


def _valid_field(key: str, value) -> bool:
    if key == "hugo_symbol":
        return isinstance(value, str) and bool(value.strip())
    if key == "variant_category":
        return value in VARIANT_CATEGORIES
    if key == "wildtype":
        return isinstance(value, bool)
    if key in ("protein_change", "wildcard_protein_change"):
        return isinstance(value, str) and value.startswith("p.") and len(value) > 2
    if key == "cnv_call":
        return value in CNV_CALLS
    if key == "mmr_status":
        return value in MMR_VALUES
    if key == "ms_status":
        return value in MS_VALUES
    if key == "variant_classification":
        return value in VARIANT_CLASSIFICATIONS
    if key == "exon":
        return isinstance(value, int) and not isinstance(value, bool)
    return False


def _validate_leaf(fields) -> dict | None:
    if not isinstance(fields, dict):
        return None
    kept = {}
    for key, value in fields.items():
        if _valid_field(key, value):
            kept[key] = value
        else:
            log.info("  dropped invalid genomic field %s", key,
                     extra={"event": "llm.genomic_invalid", "field": key,
                            "value": repr(value)[:80], "stage": "genomic"})
    if "variant_category" in kept and "wildtype" not in kept:
        # Every Mutation / CNV / SV node carries wildtype. A bare mention
        # ("KRAS mutation") asks for an alteration, so false.
        kept["wildtype"] = False
        log.info("  defaulted wildtype=false on %s", kept["variant_category"],
                 extra={"event": "llm.genomic_wildtype_default",
                        "variant_category": kept["variant_category"], "stage": "genomic"})
    return {"genomic": kept} if kept else None


def validate_genomic_node(node) -> dict | None:
    """``node`` with clinical leaves and invalid genomic fields removed; None if empty."""
    if not isinstance(node, dict):
        return None
    for key in ("and", "or"):
        if key in node:
            children = [c for c in (validate_genomic_node(c) for c in node.get(key) or [])
                        if c is not None]
            if not children:
                return None
            return children[0] if len(children) == 1 else {key: children}
    if "genomic" in node:
        return _validate_leaf(node["genomic"])
    return None
