"""Transform RawAMCTrial → MatchMiner CTML (ClinicalTrialNormalized).

AMC eligibility format (from ClinicalTrials.gov, possibly modified):
  ||   line separator
  ~    primary criterion (depth 1)
  ~~   sub-criterion nested under the preceding ~ item (depth 2)
  ~~~  further nesting (depth 3, rare)

What we can derive structurally from the XML fields:
  - age_numerical clinical criterion from age_group (Adults / Children / Both)
  - disease_keywords from disease_site
  - investigator from pi

The match tree is populated with age; genomic/clinical criteria from free
text are not auto-extracted and left for manual/AI population downstream.
"""
import re
from ..schemas.raw.models import RawAMCTrial
from ..schemas.matchminer.clinical_trial import (
    CtmlArm,
    CtmlEligibility,
    CtmlEligibilityCriterion,
    CtmlStep,
    CtmlTreatmentList,
    ClinicalTrialNormalized,
)

_STATUS_MAP: dict[str, str] = {
    "OPEN TO ACCRUAL": "open to accrual",
    "CLOSED TO ACCRUAL": "closed to accrual",
    "SUSPENDED": "suspended",
    "COMPLETED": "closed to accrual",
}

_AGE_GROUP_MAP: dict[str, str] = {
    "adults": "Adult",
    "children": "Pediatric",
    "both": "Both",
    "unspecified": "Both",
}

# age_group → clinical.age_numerical constraint for the match tree
_AGE_MATCH: dict[str, str | None] = {
    "adults": ">=18",
    "children": "<18",
    "both": None,
    "unspecified": None,
}


def _normalize_status(status: str | None) -> str | None:
    if not status:
        return None
    return _STATUS_MAP.get(status.strip().upper(), status)


def _normalize_phase(phase: str | None) -> str | None:
    if not phase or phase.upper() in ("N/A", "UNSPECIFIED", "OTHER"):
        return phase
    if phase.lower().startswith("phase"):
        return phase
    return f"Phase {phase}"


def _parse_disease_sites(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(";") if s.strip()]


def _parse_eligibility(raw: str | None) -> CtmlEligibility:
    """Parse ||~-delimited AMC eligibility into a recursive CtmlEligibility.

    Depth is determined by counting leading ~ characters:
      ~text    → primary criterion (depth 1), added to inclusion/exclusion list
      ~~text   → sub_criteria of the preceding depth-1 item
      ~~~text  → sub_criteria of the preceding depth-2 item
      ~~~~text → sub_criteria of the preceding depth-3 item (lab-value tables)

    depth_stack maps depth → the CtmlEligibilityCriterion currently open at
    that depth, so any item can find its parent at depth-1.
    """
    if not raw:
        return CtmlEligibility()

    inclusion: list[CtmlEligibilityCriterion] = []
    exclusion: list[CtmlEligibilityCriterion] = []
    section: str | None = None
    depth_stack: dict[int, CtmlEligibilityCriterion] = {}

    for segment in raw.split("||"):
        stripped = segment.strip()
        lower = stripped.lower()

        if re.search(r"inclusion criteria", lower):
            depth_stack.clear()
            section = "inclusion"
            continue
        if re.search(r"exclusion criteria", lower):
            depth_stack.clear()
            section = "exclusion"
            continue
        if section is None or not stripped:
            continue

        depth = len(stripped) - len(stripped.lstrip("~"))
        text = stripped.lstrip("~").strip()
        if not text:
            continue

        criterion = CtmlEligibilityCriterion(text=text)
        depth_stack[depth] = criterion

        if depth == 1:
            if section == "inclusion":
                inclusion.append(criterion)
            else:
                exclusion.append(criterion)
        else:
            parent = depth_stack.get(depth - 1)
            if parent is not None:
                parent.sub_criteria.append(criterion)
            else:
                # No parent at expected depth; promote to top-level
                if section == "inclusion":
                    inclusion.append(criterion)
                else:
                    exclusion.append(criterion)

    return CtmlEligibility(inclusion=inclusion, exclusion=exclusion)


def _age_match_criteria(age_group: str | None) -> list[dict]:
    constraint = _AGE_MATCH.get((age_group or "").strip().lower())
    if constraint is None:
        return []
    return [{"clinical": {"age_numerical": constraint}}]


def _build_treatment_list(age_group: str | None) -> CtmlTreatmentList:
    return CtmlTreatmentList(
        step=[CtmlStep(
            step_internal_id=1,
            step_code="1",
            step_type="Registration",
            match=_age_match_criteria(age_group),
            arm=[CtmlArm(
                arm_internal_id=1,
                arm_code="ARM 1",
                arm_description="Enrollment",
                arm_suspended="N",
                match=[],
                dose_level=[],
            )],
        )]
    )


def _build_summary(
    trial: RawAMCTrial,
    status: str | None,
    phase: str | None,
    age_group: str | None,
) -> dict:
    return {
        "status": [{"value": status or ""}],
        "short_title": trial.title,
        "long_title": trial.full_title,
        "phase": phase,
        "sponsor": trial.sponsor_type,
        "age": age_group,
        "gender": "All",
        "disease_keywords": _parse_disease_sites(trial.disease_site),
        "drugs": [],
        "investigator": {"full_name": trial.pi} if trial.pi else None,
        "prior_treatment_requirements": [],
    }


def to_ctml(trial: RawAMCTrial) -> ClinicalTrialNormalized:
    status = _normalize_status(trial.status)
    phase = _normalize_phase(trial.phase)
    age_group = _AGE_GROUP_MAP.get((trial.age_group or "").strip().lower(), trial.age_group)

    return ClinicalTrialNormalized(
        protocol_no=trial.protocol_no,
        nct_id=trial.nct_number,
        status=status,
        entity="amc",
        treatment_list=_build_treatment_list(trial.age_group),
        eligibility=_parse_eligibility(trial.eligibility),
        summary=_build_summary(trial, status, phase, age_group),
        raw=trial.model_dump(),
    )


def to_ctml_dict(trial: RawAMCTrial) -> dict:
    d = to_ctml(trial).model_dump()
    d["_summary"] = d.pop("summary")
    d["_raw"] = d.pop("raw")
    return d
