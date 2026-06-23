"""Clinical trial data transformer.

Raw input: see ctm/schemas/raw/clinical_trial.py
Processed output: ClinicalTrial (ctm/schemas/processed/clinical_trial.py)

Word doc form is not yet implemented — JSON only.
"""
from ctm.schemas.processed.models import ClinicalTrial

__version__ = "0.1.0"


def process(raw: dict) -> dict:
    model = ClinicalTrial(
        nct_id=raw.get("nct_id"),
        protocol_no=raw.get("protocol_no"),
        title=raw.get("title"),
        phase=raw.get("phase"),
        status=raw.get("trial_summary_status"),
        sponsor=raw.get("sponsor"),
        eligibility_criteria=raw.get("eligibility_criteria"),
        match_level=raw.get("match_level"),
        reason_type=raw.get("reason_type"),
        cancer_type_match=raw.get("cancer_type_match"),
        match_type=raw.get("match_type"),
    )
    return model.model_dump()
