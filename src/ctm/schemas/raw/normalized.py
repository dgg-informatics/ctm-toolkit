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
    pt_uuid: int                          # local join key; MongoDB _id is auto-assigned
    mrn: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    dob: date | None = None
    sex: str | None = None
    entity: str | None = None
    primary_dx: str | None = None
    metastasis_sites: list[str] = []


class ReportMetadata(BaseModel):
    report_uuid: int
    pt_uuid: int
    source: str                           # tempus | caris | ambry | amc_ngs | ogm | pml_rara
    test_name: str | None = None
    accession_no: str | None = None
    physician: str | None = None
    specimen_type: str | None = None
    date_collected: date | None = None
    date_received: date | None = None
    date_completed: date | None = None
    obtained_from: str | None = None
    link: str | None = None
    notes: str | None = None


class Finding(BaseModel):
    pt_uuid: int
    report_uuid: int
    source: str                           # propagated from ReportMetadata
    gene: str | None = None              # canonical HGNC symbol or biomarker name
    variant_type: str | None = None      # see _conventions sheet for allowed values
    result_summary: str | None = None    # short normalized result string
    raw: dict[str, Any] = {}            # verbatim source fields, keyed by raw_* column name
