"""Normalized Pydantic models — the MongoDB document shapes.

Three collections:
  patients        — one document per patient
  report_metadata — one document per lab report / test ordered
  findings        — one document per finding, cross-source queryable
"""
from datetime import date
from typing import Any

from pydantic import BaseModel


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
    wildtype: bool | None = None         # MUTATION/CNV only
    nucleotide_change: str | None = None # → TRUE_CDNA_CHANGE (stored, not matchable)
    raw: dict[str, Any] = {}            # every other finding column, keyed by column name
