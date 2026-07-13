"""Deterministic category → OncoTree diagnosis mapping.

Used as a pre-LLM step to populate oncotree_primary_diagnosis match nodes
from structured metadata (AMC categories, CTGov conditions) without an API call.

OncoTree parent nodes match all their children in MatchMiner, so broad terms
like "Breast" will match "Breast Invasive Ductal Carcinoma" etc.
"""
import difflib

# AMC (CAT1, CAT2, CAT3) → OncoTree name
# More specific tuples take precedence over less specific ones.
# Entries with empty strings for CAT2/CAT3 serve as fallbacks.
_AMC_MAP: dict[tuple[str, str, str], str | None] = {
    # ── Breast ──────────────────────────────────────────────────────────────
    ("Breast", "", ""):                                             "Breast",
    ("Breast", "Her2 Positive", ""):                               "Breast",
    ("Breast", "Triple Negative", ""):                             "Breast",
    ("Breast", "Hormone Receptor Positive", ""):                   "Breast",

    # ── Thoracic ────────────────────────────────────────────────────────────
    ("Thoracic", "Non-small cell lung cancer", "Stage I-III (Resectable)"): "Non-Small Cell Lung Cancer",
    ("Thoracic", "Non-small cell lung cancer", "Stage IV"):        "Non-Small Cell Lung Cancer",
    ("Thoracic", "Non-small cell lung cancer", ""):                "Non-Small Cell Lung Cancer",
    ("Thoracic", "Small cell lung cancer", "All"):                 "Small Cell Lung Cancer",
    ("Thoracic", "Small cell lung cancer", ""):                    "Small Cell Lung Cancer",
    ("Thoracic", "", ""):                                          "Lung",

    # ── Gastrointestinal ────────────────────────────────────────────────────
    ("Gastrointestinal", "Colorectal", "Advanced"):                "Colorectal Adenocarcinoma",
    ("Gastrointestinal", "Colorectal", "Localized"):               "Colorectal Adenocarcinoma",
    ("Gastrointestinal", "Colorectal", ""):                        "Colorectal Adenocarcinoma",
    ("Gastrointestinal", "Pancreatic", "Advanced"):                "Pancreatic Adenocarcinoma",
    ("Gastrointestinal", "Pancreatic", "Localized"):               "Pancreatic Adenocarcinoma",
    ("Gastrointestinal", "Pancreatic", ""):                        "Pancreatic Adenocarcinoma",
    ("Gastrointestinal", "Hepatocellular", "Advanced"):            "Hepatocellular Carcinoma",
    ("Gastrointestinal", "Hepatocellular", "Localized"):           "Hepatocellular Carcinoma",
    ("Gastrointestinal", "Hepatocellular", ""):                    "Hepatocellular Carcinoma",
    ("Gastrointestinal", "Gastroesophageal", "Advanced"):          "Esophagogastric Adenocarcinoma",
    ("Gastrointestinal", "Gastroesophageal", ""):                  "Esophagogastric Adenocarcinoma",
    ("Gastrointestinal", "Biliary", "Advanced"):                   "Biliary Tract",
    ("Gastrointestinal", "Neuroendocrine", "Advanced"):            "Gastrointestinal Neuroendocrine Tumors",
    ("Gastrointestinal", "Neuroendocrine", "Localized"):           "Gastrointestinal Neuroendocrine Tumors",
    ("Gastrointestinal", "Neuroendocrine", ""):                    "Gastrointestinal Neuroendocrine Tumors",
    ("Gastrointestinal", "", ""):                                  None,  # no valid broad GI parent

    # ── Genitourinary ───────────────────────────────────────────────────────
    ("Genitourinary", "Prostate", "Localized / Curative"):         "Prostate Adenocarcinoma",
    ("Genitourinary", "Prostate", "Metastatic Castration Resistant"): "Prostate Adenocarcinoma",
    ("Genitourinary", "Prostate", "Biochemically Recurrent / Salvage (post-surgery with returned PSA)"): "Prostate Adenocarcinoma",
    ("Genitourinary", "Prostate", ""):                             "Prostate Adenocarcinoma",
    ("Genitourinary", "Bladder", "Metastatic"):                    "Bladder Urothelial Carcinoma",
    ("Genitourinary", "Bladder", "Muscle Invasive / Perioperative"): "Bladder Urothelial Carcinoma",
    ("Genitourinary", "Bladder", ""):                              "Bladder Urothelial Carcinoma",
    ("Genitourinary", "Renal", "Metastatic"):                      "Renal Cell Carcinoma",
    ("Genitourinary", "", ""):                                     None,  # no valid broad GU parent

    # ── Gynecologic ─────────────────────────────────────────────────────────
    ("Gynecologic", "Treatment", "Ovary"):                         "Ovarian Epithelial Tumor",
    ("Gynecologic", "Treatment", "Endometrial"):                   "Endometrial Carcinoma",
    ("Gynecologic", "Treatment", ""):                              None,  # no valid broad gyn parent
    ("Gynecologic", "", ""):                                       None,

    # ── Head, Neck and Endocrine ────────────────────────────────────────────
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", "Laryngeal"):   "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", "Nasopharyngeal"): "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", "Oral Cavity"): "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", "Oropharyngeal"): "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", "Other Sites"): "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Locally Advanced Head and Neck", ""):            "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Recurrent/Metastatic Head and Neck", "EBV+ Nasopharyngeal"): "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Recurrent/Metastatic Head and Neck", "HPV+ Oropharyngeal"):  "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Recurrent/Metastatic Head and Neck", "Other Sites"):          "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Recurrent/Metastatic Head and Neck", ""):                     "Head and Neck Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Cutaneous Squamous Cell", "Adjuvant"):           "Cutaneous Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Cutaneous Squamous Cell", "Advanced or Metastatic"): "Cutaneous Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Cutaneous Squamous Cell", "Primary Tumor only"): "Cutaneous Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Cutaneous Squamous Cell", ""):                   "Cutaneous Squamous Cell Carcinoma",
    ("Head, Neck and Endocrine", "Thyroid", "Anaplastic"):                         "Anaplastic Thyroid Cancer",
    ("Head, Neck and Endocrine", "Thyroid", "Differentiated"):                     "Thyroid",
    ("Head, Neck and Endocrine", "Thyroid", ""):                                   "Thyroid",
    ("Head, Neck and Endocrine", "Salivary Gland", "All"):                         "Salivary Carcinoma",
    ("Head, Neck and Endocrine", "Salivary Gland", "MET"):                         "Salivary Carcinoma",
    ("Head, Neck and Endocrine", "Endocrine-Other", "All"):                        "Thyroid",
    ("Head, Neck and Endocrine", "", ""):                                          "Head and Neck",

    # ── Hematological Malignancies ──────────────────────────────────────────
    ("Hematological Malignancies", "Leukemia", "AML"):             "Acute Myeloid Leukemia",
    ("Hematological Malignancies", "Lymphoma B-cell", "CLL/SLL"):  "Chronic Lymphocytic Leukemia/Small Lymphocytic Lymphoma",
    ("Hematological Malignancies", "Lymphoma B-cell", "Follicular"): "Follicular Lymphoma",
    ("Hematological Malignancies", "Lymphoma B-cell", "Mantle Cell"): "Mantle Cell Lymphoma",
    ("Hematological Malignancies", "Lymphoma B-cell", "Marginal Zone"): "Marginal Zone Lymphoma",
    ("Hematological Malignancies", "Lymphoma B-cell", "Waldenstrom"): "Waldenstrom Macroglobulinemia",
    ("Hematological Malignancies", "Lymphoma B-cell", ""):         None,  # no valid broad B-cell parent
    ("Hematological Malignancies", "Lymphoma T-cell", "Relapsed / Refractory"): None,  # no valid broad T-cell parent
    ("Hematological Malignancies", "Lymphoma T-cell", ""):         None,
    ("Hematological Malignancies", "MPNs", "ET"):                  "Essential Thrombocythemia",
    ("Hematological Malignancies", "MPNs", "Myelofibrosis"):       "Primary Myelofibrosis",
    ("Hematological Malignancies", "MPNs", "PV"):                  "Polycythemia Vera",
    ("Hematological Malignancies", "MPNs", ""):                    "Myeloproliferative Neoplasms",
    ("Hematological Malignancies", "Multiple Myeloma", "Multiple Myeloma"): "Plasma Cell Myeloma",
    ("Hematological Malignancies", "Multiple Myeloma", "Amyloid"):  "Plasma Cell Myeloma",
    ("Hematological Malignancies", "Multiple Myeloma", ""):         "Plasma Cell Myeloma",
    ("Hematological Malignancies", "", ""):                         None,  # no valid broad heme parent

    # ── Childhood Cancers ───────────────────────────────────────────────────
    ("Childhood Cancers", "Leukemia and Lymphoma", "ALL"):         "B-Lymphoblastic Leukemia/Lymphoma",
    ("Childhood Cancers", "Leukemia and Lymphoma", "AML"):         "Acute Myeloid Leukemia",
    ("Childhood Cancers", "Leukemia and Lymphoma", "Lymphoma"):    None,  # no valid broad lymphoma parent
    ("Childhood Cancers", "Leukemia and Lymphoma", "Biology and Translational"): None,
    ("Childhood Cancers", "Leukemia and Lymphoma", "Cancer Control and Supportive Care"): None,
    ("Childhood Cancers", "Leukemia and Lymphoma", ""):            None,  # no valid broad leukemia parent
    ("Childhood Cancers", "Solid Tumor", "CNS"):                   "CNS/Brain",
    ("Childhood Cancers", "Solid Tumor", "Germ Cell"):             None,  # no single germ cell parent in OncoTree
    ("Childhood Cancers", "Solid Tumor", "Hepatoblastoma/HCC"):    "Liver",
    ("Childhood Cancers", "Solid Tumor", "RCC/Wilm's Tumor"):      "Wilms' Tumor",
    ("Childhood Cancers", "Solid Tumor", "Sarcoma"):               "Sarcoma, NOS",
    ("Childhood Cancers", "Solid Tumor", "Biology and Translational"): None,
    ("Childhood Cancers", "Solid Tumor", "Cancer Control and Supportive Care"): None,
    ("Childhood Cancers", "Solid Tumor", ""):                      None,
    ("Childhood Cancers", "Phase 1", ""):                          None,
    ("Childhood Cancers", "", ""):                                  None,

    # ── Cutaneous ───────────────────────────────────────────────────────────
    ("Cutaneous", "Cutaneous Melanoma", "Advanced"):               "Melanoma",
    ("Cutaneous", "Cutaneous Melanoma", "Primary Tumor only"):     "Melanoma",
    ("Cutaneous", "Cutaneous Melanoma", ""):                       "Melanoma",
    ("Cutaneous", "", ""):                                         "Skin",

    # ── Neurologic ──────────────────────────────────────────────────────────
    ("Neurologic", "Gliomas", ""):                                 "Diffuse Glioma",
    ("Neurologic", "Brain and Spine Metastases", ""):              None,  # metastases, not primary
    ("Neurologic", "Other", ""):                                   "CNS/Brain",
    ("Neurologic", "", ""):                                        "CNS/Brain",

    # ── Connective Tissue ───────────────────────────────────────────────────
    ("Connective Tissue", "Bone", "Metastatic"):                   "Bone",
    ("Connective Tissue", "Bone", ""):                             "Bone",
    ("Connective Tissue", "Soft Tissue", "Localized"):             "Soft Tissue",
    ("Connective Tissue", "Soft Tissue", "Metastatic"):            "Soft Tissue",
    ("Connective Tissue", "", ""):                                  "Sarcoma, NOS",

    # ── Bone Marrow Transplant ──────────────────────────────────────────────
    ("Bone Marrow Transplant", "Malignancy", "Leukemia"):          None,  # no valid broad leukemia parent
    ("Bone Marrow Transplant", "Malignancy", "Lymphoma"):          None,  # no valid broad lymphoma parent
    ("Bone Marrow Transplant", "Malignancy", "Myeloma"):           "Plasma Cell Myeloma",
    ("Bone Marrow Transplant", "Malignancy", ""):                  None,
    ("Bone Marrow Transplant", "", ""):                            None,  # supportive care

    # ── No oncotree mapping (supportive/prevention/multi-tumor) ─────────────
    ("Cancer Control and Prevention", "", ""):                     None,
    ("Multi-tumor Experimental Therapeutics", "", ""):             None,
}


def amc_categories_to_oncotree(categories: list[dict]) -> list[str]:
    """Map a list of AMC category dicts to unique OncoTree names."""
    results: list[str] = []
    for cat in categories:
        key = (cat.get("cat1", ""), cat.get("cat2", ""), cat.get("cat3", ""))
        # Try exact match first, then fallbacks with progressively fewer levels
        for lookup in [key, (key[0], key[1], ""), (key[0], "", "")]:
            if lookup in _AMC_MAP:
                onco = _AMC_MAP[lookup]
                if onco and onco not in results:
                    results.append(onco)
                break
    return results


# CTGov conditions are free text — keyword-based matching, longest match wins.
# Ordered so more specific terms appear before broader ones.
_CTGOV_KEYWORDS: list[tuple[str, str]] = [
    # Heme
    ("acute myeloid leukemia", "Acute Myeloid Leukemia"),
    ("acute lymphoblastic leukemia", "B-Lymphoblastic Leukemia/Lymphoma"),
    ("b-all", "B-Lymphoblastic Leukemia/Lymphoma"),
    ("b all", "B-Lymphoblastic Leukemia/Lymphoma"),
    ("t-all", "T-Lymphoblastic Leukemia/Lymphoma"),
    ("chronic lymphocytic leukemia", "Chronic Lymphocytic Leukemia/Small Lymphocytic Lymphoma"),
    ("cll", "Chronic Lymphocytic Leukemia/Small Lymphocytic Lymphoma"),
    ("follicular lymphoma", "Follicular Lymphoma"),
    ("mantle cell lymphoma", "Mantle Cell Lymphoma"),
    ("marginal zone lymphoma", "Marginal Zone Lymphoma"),
    ("waldenstrom", "Waldenstrom Macroglobulinemia"),
    ("diffuse large b-cell lymphoma", "Diffuse Large B-Cell Lymphoma, NOS"),
    ("dlbcl", "Diffuse Large B-Cell Lymphoma, NOS"),
    ("hodgkin lymphoma", "Hodgkin Lymphoma"),
    ("t-cell lymphoma", None),  # no valid broad T-cell parent in OncoTree
    ("multiple myeloma", "Plasma Cell Myeloma"),
    ("myelofibrosis", "Primary Myelofibrosis"),
    ("polycythemia vera", "Polycythemia Vera"),
    ("essential thrombocythemia", "Essential Thrombocythemia"),
    ("myeloproliferative", "Myeloproliferative Neoplasms"),
    ("leukemia", None),   # no valid broad leukemia parent in OncoTree
    ("lymphoma", None),   # no valid broad lymphoma parent in OncoTree
    ("myeloma", "Plasma Cell Myeloma"),
    # Solid
    ("non-small cell lung", "Non-Small Cell Lung Cancer"),
    ("nsclc", "Non-Small Cell Lung Cancer"),
    ("small cell lung", "Small Cell Lung Cancer"),
    ("sclc", "Small Cell Lung Cancer"),
    ("lung cancer", "Lung"),
    ("breast cancer", "Breast"),
    ("breast", "Breast"),
    ("colorectal", "Colorectal Adenocarcinoma"),
    ("colon cancer", "Colorectal Adenocarcinoma"),
    ("rectal cancer", "Colorectal Adenocarcinoma"),
    ("pancreatic", "Pancreatic Adenocarcinoma"),
    ("hepatocellular", "Hepatocellular Carcinoma"),
    ("cholangiocarcinoma", "Intrahepatic Cholangiocarcinoma"),
    ("biliary", "Biliary Tract"),
    ("gastric", "Esophagogastric Adenocarcinoma"),
    ("esophageal", "Esophagogastric Adenocarcinoma"),
    ("gastroesophageal", "Esophagogastric Adenocarcinoma"),
    ("prostate", "Prostate Adenocarcinoma"),
    ("bladder", "Bladder Urothelial Carcinoma"),
    ("urothelial", "Bladder Urothelial Carcinoma"),
    ("renal cell carcinoma", "Renal Cell Carcinoma"),
    ("renal cancer", "Renal Cell Carcinoma"),
    ("kidney cancer", "Renal Cell Carcinoma"),
    ("ovarian", "Ovarian Epithelial Tumor"),
    ("endometrial", "Endometrial Carcinoma"),
    ("cervical", None),  # too many subtypes; no safe broad parent
    ("melanoma", "Melanoma"),
    ("glioblastoma", "Diffuse Glioma"),  # Glioblastoma Multiforme not in OncoTree
    ("glioma", "Diffuse Glioma"),
    ("meningioma", "Meningioma"),
    ("germ cell", None),  # no single germ cell parent in OncoTree
    ("thyroid", "Thyroid"),
    ("head and neck", "Head and Neck Squamous Cell Carcinoma"),
    ("squamous cell carcinoma of the head", "Head and Neck Squamous Cell Carcinoma"),
    ("nasopharyngeal", "Head and Neck Squamous Cell Carcinoma"),
    ("oropharyngeal", "Head and Neck Squamous Cell Carcinoma"),
    ("salivary gland", "Salivary Carcinoma"),
    ("soft tissue sarcoma", "Soft Tissue"),
    ("sarcoma", "Sarcoma, NOS"),
    ("osteosarcoma", "Osteosarcoma"),
    ("ewing sarcoma", "Ewing Sarcoma"),
    ("rhabdomyosarcoma", "Rhabdomyosarcoma"),
    ("wilms", "Wilms' Tumor"),
    ("hepatoblastoma", "Hepatoblastoma"),
    ("neuroblastoma", "Neuroblastoma"),
    ("medulloblastoma", "Medulloblastoma"),
    ("neuroendocrine", "Gastrointestinal Neuroendocrine Tumors"),
]


def ctgov_conditions_to_oncotree(conditions: list[str]) -> list[str]:
    """Map CTGov condition strings to OncoTree names via keyword matching."""
    results: list[str] = []
    for condition in conditions:
        lower = condition.lower()
        for keyword, onco in _CTGOV_KEYWORDS:
            if keyword in lower:
                if onco and onco not in results:
                    results.append(onco)
                break
    return results


def diagnoses_to_match_node(diagnoses: list[str]) -> dict | None:
    """Convert a list of OncoTree names to a CTML match node (OR if multiple)."""
    if not diagnoses:
        return None
    if len(diagnoses) == 1:
        return {"clinical": {"oncotree_primary_diagnosis": diagnoses[0]}}
    return {"or": [{"clinical": {"oncotree_primary_diagnosis": d}} for d in diagnoses]}


# Common clinical terminology → correct OncoTree name.
# None means the term has no valid OncoTree parent and should be dropped.
_ONCOTREE_CORRECTIONS: dict[str, str | None] = {
    "Multiple Myeloma":                      "Plasma Cell Myeloma",
    "Glioblastoma Multiforme":               "Diffuse Glioma",
    "Glioma":                                "Diffuse Glioma",
    "Glioblastoma":                          "Diffuse Glioma",
    "Colorectal Cancer":                     "Colorectal Adenocarcinoma",
    "Colon Cancer":                          "Colorectal Adenocarcinoma",
    "Rectal Cancer":                         "Colorectal Adenocarcinoma",
    "Germ Cell Tumor":                       None,
    "Leukemia":                              None,
    "Lymphoma":                              None,
    "B-Cell Lymphoma":                       None,
    "T-Cell Lymphoma":                       None,
    "Biliary Tract Cancer":                  "Biliary Tract",
    "Esophagogastric Cancer":                "Esophagogastric Adenocarcinoma",
    "Gastric Cancer":                        "Esophagogastric Adenocarcinoma",
    "Gastrointestinal Cancer":               None,
    "Gastrointestinal Neuroendocrine Tumor": "Gastrointestinal Neuroendocrine Tumors",
    "Gynecologic Cancer":                    None,
    "Genitourinary Cancer":                  None,
    "Hematologic Malignancy":               None,
    "Head and Neck Cancer":                  "Head and Neck",
    "Salivary Gland Cancer":                 "Salivary Carcinoma",
    "Thyroid Cancer":                        "Thyroid",
    "Differentiated Thyroid Cancer":         "Thyroid",
    "Bone Cancer":                           "Bone",
    "Soft Tissue Sarcoma":                   "Soft Tissue",
    "Sarcoma":                               "Sarcoma, NOS",
    "CNS Cancer":                            "CNS/Brain",
    "Skin Cancer":                           "Skin",
    "Cervical Cancer":                       None,
    "Waldenstrom Macroglobulinemia/Lymphoplasmacytic Lymphoma": "Waldenstrom Macroglobulinemia",
    "Diffuse Large B-Cell Lymphoma":         "Diffuse Large B-Cell Lymphoma, NOS",
    "Myeloproliferative Neoplasm":           "Myeloproliferative Neoplasms",
    "Wilms Tumor":                           "Wilms' Tumor",
    "Non-Seminoma Malignant Germ Cell Tumor": "Non-Seminomatous Germ Cell Tumor",
    "Seminoma Malignant Germ Cell Tumor":    "Seminoma",
    "Ovarian Immature Teratoma":             "Immature Teratoma",
    "Pure Immature Teratoma":                "Immature Teratoma",
    "Pure Mature Teratoma":                  "Mature Teratoma",
    "Pure Dysgerminoma":                     "Dysgerminoma",
    "Extragonadal Germ Cell Tumor":          "Extra Gonadal Germ Cell Tumor",
    "Primary Central Nervous System Germ Cell Tumor": "Germ Cell Tumor, Brain",
    "Ovarian":                               "Ovarian Germ Cell Tumor",
    "Testicular":                            None,  # too vague
    "Spermatocytic Seminoma":                None,  # not in OncoTree
    "Relapsed Acute Lymphoblastic Leukemia": "B-Lymphoblastic Leukemia/Lymphoma",
    "Atrial Fibrillation/Flutter":           None,  # not a cancer diagnosis
    "Bone Marrow Failure Syndrome":          None,
    "Bowel Obstruction":                     None,
    "Chronic Inflammatory Bowel Disease":    None,
    "L2":                                    None,  # leukemia morphology class, not oncotree
}


def fix_oncotree_name(value: str, valid: set[str]) -> str | None:
    """Validate and correct an oncotree_primary_diagnosis value.

    Handles the '!' exclusion prefix. Resolution order:
      1. exact match in valid set → return as-is
      2. corrections map → return corrected (or None to drop)
      3. fuzzy match (cutoff=0.85) → return closest
      4. None → drop
    """
    prefix = "!" if value.startswith("!") else ""
    raw = value[1:] if prefix else value

    if raw in valid:
        return value

    if raw in _ONCOTREE_CORRECTIONS:
        corrected = _ONCOTREE_CORRECTIONS[raw]
        return f"{prefix}{corrected}" if corrected else None

    matches = difflib.get_close_matches(raw, valid, n=1, cutoff=0.85)
    return f"{prefix}{matches[0]}" if matches else None
