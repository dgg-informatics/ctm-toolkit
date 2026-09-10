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


# A protein change, minus its "p." prefix: an amino acid, a codon number, then
# whatever describes the change — "R", "*", "FS*12", "_A750DEL", "INSASV", "=",
# "EXT*17", a closing paren. Requiring the tail to be whitespace-free is what
# tells a real protein change from free text ("EXON 19 DELETION", "SPLICE SITE").
#
# The three-letter codes are here for RECOGNITION ONLY — so "Leu858Arg" is
# understood to be a protein change and isn't reported as junk. They are never
# translated: it is stored as "p.LEU858ARG", not "p.L858R".
#
# IGNORECASE because the check runs before uppercasing, so a curator's lowercase
# "l858r" is recognized rather than flagged.
_AMINO_ACID = (
    r"(?:Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|"
    r"Trp|Tyr|Val|Ter|Sec|Xaa|[ACDEFGHIKLMNPQRSTVWYBZXU*])"
)
_PROTEIN_CHANGE_RE = re.compile(rf"^\(?{_AMINO_ACID}\d+\S*$", re.IGNORECASE)


def _normalize_protein_change(v: object) -> str | None:
    """Store every protein change as 'p.' + UPPERCASE; blank becomes None.

    matchengine compares TRUE_PROTEIN_CHANGE as an exact string, so both the
    prefix and the casing are fixed here regardless of what the curator typed —
    'l858r', 'L858R' and 'P.L858R' all land on 'p.L858R'.

    A value that isn't shaped like a protein change is prefixed all the same
    (free text becomes 'p.EXON 19 DELETION'); it can never match, so it is
    reported at transform time instead — see _is_malformed_protein_change.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s[:2].upper() == "P.":
        s = s[2:]
    return f"p.{s.upper()}"


def _is_malformed_protein_change(v: str | None) -> bool:
    """True for a stored protein change whose payload isn't shaped like one —
    it will never match, so the curator needs to hear about it."""
    return bool(v) and not _PROTEIN_CHANGE_RE.match(v.removeprefix("p."))


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
