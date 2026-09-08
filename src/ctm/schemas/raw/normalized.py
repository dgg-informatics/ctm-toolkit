"""Normalized Pydantic models — the MongoDB document shapes.

Three collections:
  patients        — one document per patient
  report_metadata — one document per lab report / test ordered
  findings        — one document per finding, cross-source queryable
"""
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, field_validator


def _normalize_wildtype(v: object) -> str | None:
    """Normalize a wildtype cell to 'true' / 'false' / 'indeterminate', or None
    when blank. Any other value is returned lowercased as-is so the transformer
    can flag it — the column must be TRUE, FALSE, or INDETERMINATE."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip().lower()
    return s or None


# HGVS protein change, minus its "p." prefix: an amino acid (three-letter form
# preferred by the alternation, else one-letter or the stop codon "*"), a codon
# number, then whatever describes the change — "R", "*", "fs*12", "_A750del",
# "insASV", "=", "ext*17", a closing paren. Requiring the tail to be
# whitespace-free is what keeps free text ("Exon 19 deletion", "splice site")
# out; the match is case-sensitive, so "c.2573T>G" and a lowercase "l858r" fall
# through untouched rather than being mangled into something unmatchable.
_AMINO_ACID = (
    r"(?:Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|"
    r"Trp|Tyr|Val|Ter|Sec|Xaa|[ACDEFGHIKLMNPQRSTVWYBZXU*])"
)
_PROTEIN_CHANGE_RE = re.compile(rf"^\(?{_AMINO_ACID}\d+\S*$")


def _normalize_protein_change(v: object) -> str | None:
    """Ensure an HGVS-shaped protein change carries its 'p.' prefix.

    matchengine matches TRUE_PROTEIN_CHANGE as an exact string, so a curator's
    bare 'L858R' silently never matches a 'p.L858R' trial clause. Only values
    that actually look like a protein change are prefixed; anything else is
    returned exactly as typed, to be flagged at transform time rather than
    corrupted (see _is_unprefixed_protein_change).
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s[:2] in ("p.", "P."):
        return "p." + s[2:]
    return f"p.{s}" if _PROTEIN_CHANGE_RE.match(s) else s


def _is_unprefixed_protein_change(v: str | None) -> bool:
    """True for a stored protein change that can never match — anything
    non-blank that _normalize_protein_change could not resolve to 'p.…'."""
    return bool(v) and not v.startswith("p.")


class Patient(BaseModel):
    pt_uuid: str                          # join key, e.g. "pt_0000001"; MongoDB _id is auto-assigned
    mrn: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    dob: date | None = None
    sex: str | None = None
    vital_status: str | None = None
    entity: str | None = None
    primary_dx: str | None = None
    oncotree_primary_diagnosis: str | None = None
    metastasis_sites: list[str] = []
    referring_clinician: str | None = None
    source: str | None = None            # how this patient row was captured, e.g. "manual"
    trb_date: date | None = None         # when the patient was seen at tumor review board
    raw: dict[str, Any] = {}            # any other pt_general column, keyed by column name


class ReportMetadata(BaseModel):
    report_uuid: str                      # join key, e.g. "rp_0000001"
    pt_uuid: str
    source: str                           # tempus | caris | ambry | amc_ngs | ogm | pml_rara
    test_name: str | None = None
    # Normalized handle on the paper report; _source says which id it came from
    # (accession_no | case_no | order_number), so it can be traced to the PDF.
    unique_test_id: str | None = None
    unique_test_id_source: str | None = None
    ordering_physician: str | None = None
    raw: dict[str, Any] = {}            # every other report column, keyed by column name


class Finding(BaseModel):
    pt_uuid: str
    report_uuid: str
    source: str                           # propagated from ReportMetadata
    biomarker: str | None = None         # HGNC symbol or marker name → TRUE_HUGO_SYMBOL
    variant_category: str | None = None  # MUTATION | CNV | SIGNATURE | SV | Other
    protein_change: str | None = None    # → TRUE_PROTEIN_CHANGE (exact match)
    cnv_call: str | None = None          # CNV only; friendly label, remapped in the transformer
    signature_level: str | None = None   # SIGNATURE only: Deficient | Proficient | Stable
    wildtype: str | None = None          # MUTATION/CNV/SV: true | false | indeterminate
    nucleotide_change: str | None = None # → TRUE_CDNA_CHANGE (stored, not matchable)
    raw: dict[str, Any] = {}            # every other finding column, keyed by column name

    @field_validator("wildtype", mode="before")
    @classmethod
    def _wildtype(cls, v: object) -> str | None:
        return _normalize_wildtype(v)

    @field_validator("protein_change", mode="before")
    @classmethod
    def _protein_change(cls, v: object) -> str | None:
        return _normalize_protein_change(v)
