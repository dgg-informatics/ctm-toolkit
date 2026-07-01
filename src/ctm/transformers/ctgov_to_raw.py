"""Parse and fetch ClinicalTrials.gov API v2 studies → RawCTGovTrial.

The API v2 endpoint is:
    GET https://clinicaltrials.gov/api/v2/studies
    GET https://clinicaltrials.gov/api/v2/studies/{nctId}

Each item in the `studies` list (or the single study response) is accepted
by from_study().  Only protocolSection fields are used — derivedSection is
ignored for now.

DB storage pattern (MongoDB):
    trial = fetch("NCT03067181")
    db.ctgov_trials.replace_one({"nct_id": trial.nct_id}, trial.model_dump(), upsert=True)
"""
import json
import urllib.error
import urllib.request

from ..schemas.raw.models import RawCTGovTrial

_BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

_DRUG_TYPES = {"DRUG", "BIOLOGICAL"}


def from_study(study: dict) -> RawCTGovTrial:
    """Convert a single CTGov API v2 study dict → RawCTGovTrial."""
    ps = study.get("protocolSection", {})

    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    sponsor = ps.get("sponsorCollaboratorsModule", {})
    desc = ps.get("descriptionModule", {})
    conditions = ps.get("conditionsModule", {})
    design = ps.get("designModule", {})
    arms = ps.get("armsInterventionsModule", {})
    elig = ps.get("eligibilityModule", {})
    contacts = ps.get("contactsLocationsModule", {})

    pi: str | None = None
    for official in contacts.get("overallOfficials", []):
        if official.get("role") == "PRINCIPAL_INVESTIGATOR":
            pi = official.get("name")
            break

    drugs = [
        iv["name"]
        for iv in arms.get("interventions", [])
        if iv.get("type") in _DRUG_TYPES and iv.get("name")
    ]

    return RawCTGovTrial(
        nct_id=ident["nctId"],
        brief_title=ident.get("briefTitle"),
        official_title=ident.get("officialTitle"),
        overall_status=status.get("overallStatus"),
        phases=design.get("phases", []),
        lead_sponsor=sponsor.get("leadSponsor", {}).get("name"),
        brief_summary=desc.get("briefSummary"),
        conditions=conditions.get("conditions", []),
        sex=elig.get("sex"),
        minimum_age=elig.get("minimumAge"),
        maximum_age=elig.get("maximumAge"),
        std_ages=elig.get("stdAges", []),
        eligibility_criteria=elig.get("eligibilityCriteria"),
        principal_investigator=pi,
        drug_interventions=drugs,
    )


def from_search_response(response: dict) -> list[RawCTGovTrial]:
    """Convert the top-level search response dict (with a `studies` list)."""
    return [from_study(s) for s in response.get("studies", [])]


def fetch(nct_id: str) -> RawCTGovTrial:
    """Fetch a single study from ClinicalTrials.gov and return a RawCTGovTrial.

    Raises:
        ValueError: if the NCT ID is not found (404).
        urllib.error.URLError: on network failure.
    """
    url = f"{_BASE_URL}/{nct_id.strip()}?format=json"
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError(f"NCT ID not found: {nct_id}") from exc
        raise
    return from_study(data)
