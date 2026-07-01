"""Transform RawCTGovTrial → MatchMiner CTML (ClinicalTrialNormalized).

CTGov eligibility_criteria is markdown:
  "Inclusion Criteria:\n\n* item\n  * sub-item\n\nExclusion Criteria:\n\n* item"

What we can derive structurally from CTGov fields:
  - age_numerical from minimum_age / std_ages
  - gender from sex
  - oncotree_primary_diagnosis is NOT populated (conditions are free text, not Oncotree codes)

The match tree is populated with age and gender where available; genomic
and oncotree criteria are left for manual/AI population downstream.
"""
import re
from ..schemas.raw.models import RawCTGovTrial
from ..schemas.matchminer.clinical_trial import (
    CtmlArm,
    CtmlEligibility,
    CtmlEligibilityCriterion,
    CtmlStep,
    CtmlTreatmentList,
    ClinicalTrialNormalized,
)

_STATUS_MAP: dict[str, str] = {
    "RECRUITING": "open to accrual",
    "NOT_YET_RECRUITING": "open to accrual",
    "ENROLLING_BY_INVITATION": "open to accrual",
    "ACTIVE_NOT_RECRUITING": "closed to accrual",
    "COMPLETED": "closed to accrual",
    "TERMINATED": "closed to accrual",
    "WITHDRAWN": "closed to accrual",
    "SUSPENDED": "suspended",
    "UNKNOWN": "open to accrual",
}

_PHASE_ORDER = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]
_PHASE_DISPLAY: dict[str, str] = {
    "EARLY_PHASE1": "Early Phase I",
    "PHASE1": "Phase I",
    "PHASE2": "Phase II",
    "PHASE3": "Phase III",
    "PHASE4": "Phase IV",
    "NA": "N/A",
}

_AGE_GROUP_MAP: dict[frozenset, str] = {
    frozenset({"CHILD"}): "Pediatric",
    frozenset({"ADULT"}): "Adult",
    frozenset({"OLDER_ADULT"}): "Adult",
    frozenset({"ADULT", "OLDER_ADULT"}): "Adult",
    frozenset({"CHILD", "ADULT"}): "Both",
    frozenset({"CHILD", "ADULT", "OLDER_ADULT"}): "Both",
}

_SEX_MAP: dict[str, str] = {"ALL": "All", "MALE": "Male", "FEMALE": "Female"}


def _normalize_status(status: str | None) -> str | None:
    if not status:
        return None
    return _STATUS_MAP.get(status.strip().upper(), status)


def _normalize_phase(phases: list[str]) -> str | None:
    if not phases:
        return None
    ordered = [p for p in _PHASE_ORDER if p in phases]
    if not ordered:
        return _PHASE_DISPLAY.get(phases[0], phases[0])
    labels = [_PHASE_DISPLAY[p] for p in ordered]
    if len(labels) == 1:
        return labels[0]
    numerals = [re.sub(r".*Phase\s*", "", lbl) for lbl in labels]
    return f"Phase {'/'.join(numerals)}"


def _normalize_age_group(std_ages: list[str]) -> str | None:
    return _AGE_GROUP_MAP.get(frozenset(std_ages), "Both" if std_ages else None)


def _normalize_gender(sex: str | None) -> str | None:
    if not sex:
        return None
    return _SEX_MAP.get(sex.strip().upper(), sex)


def _parse_minimum_age(minimum_age: str | None) -> str | None:
    """Extract numeric age from strings like '18 Years', '6 Months'."""
    if not minimum_age:
        return None
    m = re.match(r"(\d+)\s*year", minimum_age, re.IGNORECASE)
    return f">={m.group(1)}" if m else None


def _parse_eligibility(criteria: str | None) -> CtmlEligibility:
    """Parse CTGov markdown eligibility into a recursive CtmlEligibility.

    CTGov format:
      "Inclusion Criteria:" / "Exclusion Criteria:" → section headers
      "* text"      → depth 1 (no indent)
      "  * text"    → depth 2 (2-space indent)
      "    * text"  → depth 3 (4-space indent)

    Depth is derived from indent // 2, matching the recursive
    CtmlEligibilityCriterion structure used by the AMC parser.
    """
    if not criteria:
        return CtmlEligibility()

    inclusion: list[CtmlEligibilityCriterion] = []
    exclusion: list[CtmlEligibilityCriterion] = []
    section: str | None = None
    depth_stack: dict[int, CtmlEligibilityCriterion] = {}

    for line in criteria.splitlines():
        lower = line.strip().lower()

        if re.match(r"inclusion criteria\s*:?", lower):
            depth_stack.clear()
            section = "inclusion"
            continue
        if re.match(r"exclusion criteria\s*:?", lower):
            depth_stack.clear()
            section = "exclusion"
            continue
        if section is None or not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        text = re.sub(r"^\*+\s*", "", line.strip()).strip()
        if not text:
            continue

        depth = max(1, indent // 2 + 1)
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
                if section == "inclusion":
                    inclusion.append(criterion)
                else:
                    exclusion.append(criterion)

    return CtmlEligibility(inclusion=inclusion, exclusion=exclusion)


def _age_match_criteria(minimum_age: str | None, gender: str | None) -> list[dict]:
    """Build clinical match node from age and gender where available."""
    clinical: dict = {}
    age_constraint = _parse_minimum_age(minimum_age)
    if age_constraint:
        clinical["age_numerical"] = age_constraint
    if gender and gender != "All":
        clinical["gender"] = gender
    if not clinical:
        return []
    return [{"clinical": clinical}]


def _build_treatment_list(minimum_age: str | None, gender: str | None) -> CtmlTreatmentList:
    return CtmlTreatmentList(
        step=[CtmlStep(
            step_internal_id=1,
            step_code="1",
            step_type="Registration",
            match=_age_match_criteria(minimum_age, gender),
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
    trial: RawCTGovTrial,
    status: str | None,
    phase: str | None,
    age_group: str | None,
    gender: str | None,
) -> dict:
    return {
        "status": [{"value": status or ""}],
        "short_title": trial.brief_title,
        "long_title": trial.official_title,
        "phase": phase,
        "sponsor": trial.lead_sponsor,
        "age": age_group,
        "gender": gender,
        "disease_keywords": list(trial.conditions),
        "drugs": [{"name": d} for d in trial.drug_interventions],
        "investigator": {"full_name": trial.principal_investigator} if trial.principal_investigator else None,
        "prior_treatment_requirements": [],
    }


def to_ctml(trial: RawCTGovTrial) -> ClinicalTrialNormalized:
    status = _normalize_status(trial.overall_status)
    phase = _normalize_phase(trial.phases)
    age_group = _normalize_age_group(trial.std_ages)
    gender = _normalize_gender(trial.sex)

    return ClinicalTrialNormalized(
        protocol_no=None,
        nct_id=trial.nct_id,
        status=status,
        entity="ctgov",
        treatment_list=_build_treatment_list(trial.minimum_age, gender),
        eligibility=_parse_eligibility(trial.eligibility_criteria),
        summary=_build_summary(trial, status, phase, age_group, gender),
        raw=trial.model_dump(),
    )


def to_ctml_dict(trial: RawCTGovTrial) -> dict:
    d = to_ctml(trial).model_dump()
    d["_summary"] = d.pop("summary")
    d["_raw"] = d.pop("raw")
    return d
