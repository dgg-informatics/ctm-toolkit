# Michigan Medicine Clinical Trial Integration Toolkit ⚒️

This repo prepares data from various sources to integrate with popular open-source clinical trial matching software (MatchMiner supported, TrialMatchAI at some point). Beyond providing input sources for popular clinical trial matching software, this repo also provides post-matching processing scripts to generate reports. For instance, once clinical trial matching has completed, it interfaces with the structured output (from MatchMiner) to provide a report for the patient.

## Commands

| Command | Purpose |
| --- | --- |
| `ctm-mm patients` | Excel workbook → MatchMiner-compatible `{clinical, genomic, extras}` JSON |
| `ctm-mm trials` | AMC XML / Sparrow XLSX / West XLSX / CTGov JSON → CTML-staged trial JSON |
| `ctm-mm trials-diff` | Split a fresh normalization into unchanged / changed / deleted vs. the previous master |
| `ctm-mm trials-curate` | **[deprecated]** Alias for `ctm-llm biomarkers`; removed in 2.0.0 |
| `ctm-mm trials-confidence-split` | **[beta]** Bucket curated trials into auto-pass / needs-a-human |
| `ctm-mm trials-merge` | Merge carried-forward and freshly-curated trials into a new dated master |
| `ctm-llm general` | LLM pass drafting CTML match nodes from each eligibility criterion and the trial title |
| `ctm-llm biomarkers` | LLM scan for biomarker references across titles, disease keywords, curator genes and criteria |
| `ctm-ctml` | **[deprecated]** Alias for `ctm-llm general`; removed in 2.0.0 |
| `ctm-fetch` | Fetch a single trial from ClinicalTrials.gov by NCT ID |
| `ctm-meta` | Multi-section CSV comparing coverage, overlap, and status across trial sources |
| `ctm-report` | Build the trial-match report as a PDF, or serve a live-reload preview |

Every command supports `--help`.

## Quick Start

#### Patient Data Prep

```bash
# Step 1: Normalize patient data from a filled-in Excel workbook
ctm-mm patients <patient_data.xlsx> --pt-uuid 1234 --out pt_1234.json
```

Need a blank workbook to fill in? Run `python scripts/make_template.py` — it writes
`patient_data_template.xlsx` into the current directory. For the expected layout,
see `tests/fixtures/test-pt-data-v0.0.1.xlsx`, which carries mock patients plus a
sample row for every findings sheet.

#### Clinical Trial Data Prep

```bash
# Step 2: Normalize trial data from AMC's XML, Sparrow/West's XLSX, and/or ClinicalTrials.gov JSON
ctm-mm trials --amc <amc_trials.xml> --sparrow <sparrow.xlsx> --west <west.xlsx> --out trials.json
```

**Note: We can also fetch trials directly from ClinicalTrials.gov without any template:**

```bash
# Step 2 (alternate): Fetch a single trial from ClinicalTrials.gov (raw or normalized)
ctm-fetch --nct NCT03067181 --output nct-raw.json  # save raw output
# Step 2.5 (alternate): Normalize clinicaltrials.gov data to matchminer (mm)
ctm-fetch --nct NCT03067181 --output nct-normalized.json --fmt-mm  # format for matchminer
```

#### CTML Normalization

**Copy `.env.example` to `.env` and fill in `UMGPT_API_KEY` and `UMGPT_BASE_URL`**
(`UMGPT_MODEL` is optional, defaults to `gpt-4o`). LLM responses are cached in
`~/.cache/ctm/` — override with `CTM_CACHE_DIR` or `XDG_CACHE_HOME`.

#### MongoDB configuration

`ctm-mm trials-diff` stores its output in MongoDB by default. Install with
`uv pip install 'ctm-toolkit[db]'` and set these in the same `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `MONGO_HOST` | yes | e.g. `localhost` |
| `MONGO_PORT` | yes | e.g. `27018` (the `docker-compose` mongo service maps `27018:27017`) |
| `MONGO_DBNAME` | yes | **This run's** database, e.g. `2026-08-17_dev`. One database per run keeps runs isolated; `--db NAME` overrides it without editing `.env` |
| `MONGO_MASTER_DBNAME` | only without `--master` | The master trial list's database. Deliberately **not** per-run — the master is rolling current state, so it has a fixed address. No default: a default here would silently resolve to an empty database and route every trial to `changed` |
| `MONGO_MASTER_COLLECTION` | no | Defaults to `06_master_trials` |

These are CTM's own Mongo settings. MatchMiner's credentials are separate and
still come from `SECRETS_JSON.json` — see "MatchMiner Preparation and Running".

```bash
# Step 3: Run the LLM (UMGPT) to help match MatchMiner's Clinical Trial Markup Language (CTML) format
ctm-llm general --trials nct-normalized.json --out ctml-draft.json --limit 2
```

Step 4: **Manual Processing**
!Ensure you do the **manual processing** from the normalized format to MATCHMINER!!! This involves translating text eligiblity criteria into MatcherMiner format.

#### Running MatchMiner

```bash
# Step 5: Load trial data into MatchMiner
matchengine load -t ctml-draft-manually-edited.json --trial-format json --db test

# Step 6: Load Patient clinical data
matchengine load -c patient-normalized-clinical.json --db test

# Step 7: Load Patient genomic data
matchengine load -g patient-normalized-genomic.json --db test

# Step 8: Run MatchMiner
matchengine match --config path/to/dfci_config.json

# Step 9: Export match results from MatchMiner's Mongo database
SECRETS_JSON=SECRETS_JSON.json python export_matches.py --patient 8 --output export/ --db v1
```

#### Build a Report

```bash
# Step 10: Build report from patient, trial, and match collections
ctm-report --pts pt_1234.json --trials trials.json --matches export/matchminer_export.json --sample-id 8 --out output.pdf
```

---

### ‼️ Warning for Patient and Clinical Trial Data ‼️

#### Patient Data

Patient data is based upon **manual recording** of patient data into a template excel sheet. Until we can automate the process of pulling a patient's file and normalizing it, this will have to do. See the reference workbook at *tests/fixtures/test-pt-data-v0.0.1.xlsx*, or generate a blank one with `python scripts/make_template.py`.

⚠️ **A filled-in workbook is PHI and must never be committed.** `.gitignore` denies
the generated `patient_data_template.xlsx` by default, but keep real workbooks
outside the repo entirely.

After you fill out the template excel sheet, please be careful of:

- ONCOTREE_PRIMARY_DIAGNOSIS_NAME passes through directly from the Excel oncotree_primary_diagnosis column - if that cell has a free-text diagnosis instead of an Oncotree code, MatchMiner won't match on it correctly
- TRUE_HUGO_SYMBOL passes through from the gene column as-is - no validation against HGNC so be careful to use a HUGO symbol
- variant_type in the Excel drives VARIANT_CATEGORY mapping - if someone enters an unrecognized value it gets skipped with a warning

#### Clinical Trial Data

Clinical trial data comes from automated sources for AMC and Sparrow. For AMC, an export of the OnCORE database is retrieved in the form of an XML file. For Sparrow, an Excel document is emailed each month.

For UMH-West, an Excel sheet is emailed in a format that is incomplete and requires manual processing immediately, so we re-record West trial data into our own sheet rather than consuming the file we receive as-is.

None of these raw source files live in this repo — they are institutional exports
and are kept out of it. Small mock samples showing the expected shape of each are
in `tests/fixtures/`:

| Source | Fixture |
|---|---|
| AMC (OnCORE XML) | `test-trials-amc-v0.0.1.xml` |
| Sparrow (XLSX) | `test-trials-sparrow-v0.0.1.xlsx` |
| UMH-West (XLSX) | `test-trials-west-v0.0.1.xlsx` |
| ClinicalTrials.gov API responses | `clinicaltrial_gov/NCT*.json` |

The automation from AMC, Sparrow, West, and ClinicalTrials.gov clinical trial data requires some manual massaging of the data before it can be put into MatchMiner:

- treatment_list.step[0].match is auto-populated with age constraints only (AMC) or age + gender for sex-restricted trials (CTGov) - all genomic and oncotree criteria must be filled in manually
- Eligibility criteria are parsed and preserved structurally (inclusion/exclusion with nesting) but are not converted into MatchMiner and/or match nodes automatically
- AMC trials have no arm structure - a single stub arm (ARM 1) is generated; if the trial has multiple arms or dose cohorts they must be added manually
- status is normalized automatically (OPEN TO ACCRUAL → open to accrual) but verify any trial with an unusual status value, as unrecognized values pass through unchanged
- AMC data has no drug/intervention field - _summary.drugs will always be empty and must be populated manually if needed for display
- protocol_no is only populated for AMC trials; CTGov trials will have null there unless manually set

---

# Background

Creating a clinical trial match involves two key categories of data:
1. patient information (query)
2. clinical trial information (reference)

Patient data must match up to trial eligibility requirements to make a match. Trial eligibility often relies on some combination of patient demographics, diagnoses, prior treatments, as well as molecular and genetic data. Combining all of this information into one spot can be difficult, thus the purpose of this toolkit is to make it easier to combine all the data sources needed into the formats required for popular clinical trial engines and report building.

For matching a patient to trials, we define 2 categories of patient data:
1. clinical data
2. genomic data

**Clinical data** is kind of an overloaded term, but it refers to general patient demographics as well as diagnoses. Examples include Age, Sex, Primary Diagnosis, Weight, etc. **Genomic data** comes from molecular testing, which is either performed in-house (AMC's Division of Diagnostic Genetics and Genomics) or from an external company (Tempus, Caris, Foundation, to name a few).

---

## 🔧 Data pipeline 🛠️


### Patient Data Preparation

**Raw --> Normalized Patient Data**

Starting input (1 file): **patient_data_template.xlsx** (excel template)
Ending output (2 files): [**patient_clinical.json**, **patient_genomic.json**]

1. Fill out patient_data_template.xlsx sheets
   1. pt_general: look up patient general information and manually record here. You can add as many columns as you want and later update the report generation script so you can include other patient information.
   2. `*_findings` sheets — one row per genomic finding. The sheets the parser reads are `tempus_findings`, `caris_findings`, `ambry_findings`, `amc_ngs_findings`, `ogm_findings`, `pml_rara_findings`, `mayo_findings`, `henry_ford_findings`, `guardant360_findings`, and `tumor_biomarkers`. The authoritative list is `SHEET_NORMALIZERS` in `src/ctm/transformers/normalize_manual.py`; `tests/test_template_sheets.py` fails if the reference workbook drifts from it. Every findings sheet shares the same 5 core columns:

      | Column | Meaning |
      |---|---|
      | `gene` | HGNC gene symbol. For a fusion with a known partner, use `GENE1::GENE2` (`/` or `-` also accepted) — e.g. `EML4::ALK`. For a fusion with an unknown partner, just the one gene name. |
      | `protein` | HGVS protein change, e.g. `p.E545K`. Leave blank for fusions and for negative/wildtype findings — there's no variant to describe. |
      | `nucleotide` | HGVS cDNA change, e.g. `c.2573T>G`. Optional; leave blank if not available. |
      | `variant_type` | Controlled vocabulary — see table below. This is the field that determines everything else about how the row gets translated. |
      | `result_summary` | Only consumed for CNV findings (populates `CNV_CALL`, unless a legacy override applies), signature findings (MMR/POLE/APOBEC/tobacco status), and TMB rows. For plain SNV/INDEL/fusion/negative findings it is **not stored anywhere** — free text here (e.g. `"46.8% VAF"`) is discarded during normalization, so don't rely on it for anything the match engine needs to see. |

      `variant_type` values and what they produce (case-sensitive — `MUTATION` is not a recognized value, use `SNV`):

      | `variant_type` | Example `gene` | Produces |
      |---|---|---|
      | `SNV` / `INDEL` | `PIK3CA`, `protein: p.E545K` | `{TRUE_HUGO_SYMBOL, VARIANT_CATEGORY: "MUTATION", WILDTYPE: false, TRUE_PROTEIN_CHANGE}` |
      | `pertinent_negative` / `negative` | `PIK3CA` (protein/nucleotide blank) | `{TRUE_HUGO_SYMBOL, VARIANT_CATEGORY: "MUTATION", WILDTYPE: true}` — gene tested, no mutation found |
      | `fusion` / `structural_variant` | `EML4::ALK` | `{TRUE_HUGO_SYMBOL: "EML4", VARIANT_CATEGORY: "SV", WILDTYPE: false, LEFT_PARTNER_GENE: "EML4", RIGHT_PARTNER_GENE: "ALK"}` |
      | `fusion_negative` | `ALK` (protein/nucleotide blank) | `{TRUE_HUGO_SYMBOL: "ALK", VARIANT_CATEGORY: "SV", WILDTYPE: true}` — no partner-gene fields, since none was found |
      | `cnv` | gene name; `result_summary` holds the call | `{..., VARIANT_CATEGORY: "CNV", CNV_CALL: <parsed from result_summary>}` |

      > [!NOTE]
      > For fusions, `LEFT_PARTNER_GENE`/`RIGHT_PARTNER_GENE` are derived automatically by splitting the `gene` column on `::`, `/`, or `-` — no separate columns needed per partner gene. Which gene ends up "left" vs "right" doesn't matter for trial matching: a trial clause like `{"hugo_symbol": "ALK", "variant_category": "Structural Variation"}` checks both fields, not just one side.
      >
      > For a wildtype/negative fusion result, use `fusion_negative`, not `negative` — `negative` would produce `VARIANT_CATEGORY: "MUTATION"` instead of `"SV"`. When writing the corresponding trial-side match clause for a negative fusion, omit `variant_category` entirely (`{"hugo_symbol": "ALK", "wildtype": true}`) — including `variant_category: "Structural Variation"` on that clause triggers matchengine's structured-SV query rewrite, which searches `LEFT_PARTNER_GENE`/`RIGHT_PARTNER_GENE` fields that a wildtype fusion record never populates, so the clause would silently match nothing.
2. Run the script to convert manual excel format into MatchMiner-compatible JSON
   1. `$ ctm-mm patients patients-raw.xlsx --out patients-normalized.json`
      1. This writes a JSON with 3 top-level fields consisting of arrays: clinical, genomic, and _extras
3. Split the JSON file into clinical and genomic entries
   1. Should have 2 files: 1 is a JSON array of clinical docs and the other a similar array of genomic docs

### Clinical Trial Data Preparation

** Raw --> CTML-Normalized Trial Data**

Starting input (3 files): [**amc-trials.xml**, **sparrow-trials.xlsx**, **west-trials.xlsx**] (raw source from entities)
Ending output (1 file): **all-trials-llm-edited.json**

This process is much more complex since we have 4 data sources (Sparrow, West, AMC, and ClinicalTrials.gov)

1. For AMC, West, and Sparrow you can point to their corresponding XML or XLSX file to create a structure very similar to Matchminer:
   1. `$ ctm-mm trials --amc trials-amc.xml --sparrow trials-sparrow.xlsx --west trials-west.xlsx --out trials-all-normalized.json`
   2. Above produces what we call a **staging file:** a .JSON file that is very similar to Clinical Trial Markup Language (CTML) format, but it is staged to be more suitable for the next LLM stage
2. Next, we run the LLM (UMGPT) on the above *staged file* to help us automate our conversion from raw to CTML-formatted data
   1. `ctm-llm general --trials trials-all-normalized.json --out trials-all-llm-draft.json`
   2. **Note:** if you just want to run the LLM on 1 trial, you can specify the `--nct <nct_number>` flag.
   3. **Note:** if you just want to run the LLM on the first N trials, you can specify the `--limit <N>` flag, such as `--limit 10`
   4. It is recommended you make a copy of this output file to retain the original output and have a separate file for doing your manual edits. Such as `cp trials-all-llm-draft.json trials-all-llm-edited.json`
3. Manually check the eligibility criteria and corresponding match clauses suggested by the LLM. Add appropriate match clauses

> [!NOTE] 
> For ClinicalTrials.gov data, you can fetch a particular trial by NCT number
> `$ ctm-fetch --nct NCT03067181 --output nct-raw.json`  # fetch clinical trials and download raw JSON
> You can also normalize the data in 1 step:
> `$ ctm-fetch --nct NCT03067181 --output nct-raw.json --fmt-mm`  # fetch clinical trials and download NORMALIZED JSON
> If you have a raw JSON doc from clinicaltrials.gov, you can normalize it with the same command as you'd normalize AMC/Sparrow/West trials:
> `$ ctm-mm trials --ct <raw-ctgov.json> --out to-normalized.json`

### Updating Trials

AMC, Sparrow, and West each send a refreshed trial sheet roughly weekly. Re-running the full LLM + manual-curation pass on every trial every week is wasteful — most trials haven't changed. `ctm-mm trials-diff` / `ctm-mm trials-merge` let you skip that work for anything whose eligibility criteria are unchanged, while keeping a permanent dated record of every trial set.

Starting input: a fresh combined normalization + the previous dated **master** trials file (e.g. `2026-07-13-trials.json` — the curated output from last week's run, already loaded into MatchMiner).
Ending output: a new dated master, e.g. `2026-07-14-trials.json`.

1. Normalize the new raw sheets, same as the first-time flow:
   1. `$ ctm-mm trials --amc <amc.xml> --sparrow <sparrow.xlsx> --west <west.xlsx> --out normalized-2026-07-14.json`
2. Diff the fresh normalization against last week's master:
   1. `$ ctm-mm trials-diff --new normalized-2026-07-14.json --master 2026-07-13-trials.json --out-prefix 2026-07-14`  # --new is the normalized data and --master is the most recent manually curated data
   2. Produces three buckets:
      - `unchanged` — eligibility identical to the master's copy. Already has curated match nodes carried forward from `2026-07-13-trials.json` untouched; every other field (status, title, etc.) is refreshed. **No LLM call, no manual review needed for these.**
      - `changed` — eligibility differs, or the trial is brand new. Not yet curated — this is the only bucket that needs the next two steps.
      - `deleted` — trials present in last week's master but absent from this week's sheets. Kept as a permanent record; nothing automated acts on it.
   3. Writes `2026-07-14-unchanged.json`, `-changed.json`, and `-deleted.json` as before, **and** stores the same split in MongoDB — the `02_diff_trials` collection of `MONGO_DBNAME`, one document per trial carrying `diff_status`, `run_date`, and `processed_with`. See "MongoDB configuration" below.
   4. `--no-disk` skips the files and stores only to MongoDB. Not useful yet: the next steps (`ctm-llm general`, `ctm-llm biomarkers`, `trials-merge`) still read file paths, so the chain needs the files until they migrate too. Disk output stays the default for the whole `1.x` line; it becomes opt-in at `2.0.0`, once every stage can read from Mongo.
   5. The master is read from the `06_master_trials` collection in `MONGO_MASTER_DBNAME` unless `--master` names a file. `--master-db` and `--master-collection` override either half.
   6. On the very first run there's no master yet — pass `--allow-empty-master` and everything routes to `changed`. Without it, an empty master is a hard error, since silently routing every trial to `changed` means re-running the full LLM curation.

> [!NOTE]
> **Fringe case — a trial changes source (`entity`) between cycles.** The identity key used to match a trial across cycles depends on `entity`: `protocol_no` for AMC, `nct_id` for everything else. If a trial moves from AMC to being tracked via West/Sparrow (or vice versa) — e.g. AMC drops it from their sheet and it starts showing up via ClinicalTrials.gov instead — its key changes basis even if the trial itself hasn't meaningfully changed. It'll show up as both `deleted` (under its old key) and `changed` (under its new key), even if `eligibility` is byte-identical to before. This is intentional, not a bug: a source migration is a real event worth a quick human glance rather than being silently absorbed as "unchanged." (The already-curated match tree isn't lost — it's just sitting in the previous dated master, so it's a quick copy-paste during the manual review of the "new" entry rather than starting from scratch.)

3. Run the LLM + manual curation, same as the first-time flow, but only on the changed file:
   1. `$ ctm-llm general --trials 2026-07-14-changed.json --out 2026-07-14-changed-draft.json`  # or omit --trials to read the 02_diff_trials `changed` documents straight from Mongo
   2. `--nct`/`--limit` aren't needed here — the file is already scoped to just the changed trials.
   3. `$ ctm-llm biomarkers --trials 2026-07-14-changed-draft.json --out 2026-07-14-changed-curated-draft.json`  # cross-checks biomarker mentions against the known-gene KB and writes them to `_llm_curation.biomarker_references`. Caches to `~/.cache/ctm/` and uses the gene/variant KB shipped inside the package, so neither `--cache` nor `--kb` needs setting.
   4. Manually check the eligibility criteria and corresponding match clauses suggested by the LLM, same as before, using the `_llm_curation` field as the starting point. Save your edits as `2026-07-14-changed-curated.json`.

> [!WARNING]
> **`ctm-mm trials-confidence-split` is a beta/experimental command.** It tries to split a `ctm-llm biomarkers` output into a "safe to auto-pass" bucket and a "needs a human curator" bucket, based on whether the trial already has a diagnosis in its match clause and whether its `biomarker_references` are all of caller-specified low-actionability types (`--allowed-biomarker-types`). The optional `--recover-diagnosis` flag makes a further LLM pass over `_raw.full_title`/`_raw.summary_obj` for trials missing a diagnosis. The thresholds and prompt are still being tuned by hand against real trial batches — don't treat its output as a substitute for the manual review step above yet. The intent is to eventually fold this logic directly into `ctm-llm` (likely as a third subcommand) rather than keep it a separate pass.

4. Merge the carried-forward and freshly-curated trials into the new master:
   1. `$ ctm-mm trials-merge --unchanged 2026-07-14-unchanged.json --changed 2026-07-14-changed-curated.json --out 2026-07-14-trials.json`
5. Load `2026-07-14-trials.json` into MatchMiner, same as the "MatchMiner Preparation and Running" step below — a date-named collection is a reasonable choice so you retain a Mongo-side historical record too.

> [!NOTE]
> Keep every dated master (`2026-07-13-trials.json`, `2026-07-14-trials.json`, ...) around rather than overwriting one file in place — the dated masters *are* the historical record. Each trial also carries a `trial_hash` field (a fingerprint of its raw source data, stamped automatically by `ctm-mm trials`) for later audit: it lets you notice a trial's metadata quietly changed under an `unchanged` routing, without forcing a real-time review of every such change.

### MatchMiner Preparation and Running

1. **Read MatchMiner docs to prepare for the next 2 steps!**
2. Load in the new data to MatchMiner
   1. `$ matchengine load -c patient-normalized-clinical.json`
   2. `$ matchengine load -g patient-normalized-genomic.json`
   3. `$ matchengine load -t trials-all-lmm-edited.json --trial-format json`
3. Execute the match!
   1. `$ matchengine match --config-path matchengine/config/dfci_config.json --extra-resources-dirs matchengine/ref`
4. Export match data from MatchMiner database
   1. `$ python export_matches.py --patient <SAMPLE_ID> --output <OUTPUT.json> --db <MONGO_DATABASE_NAME`  # the mongo database name is specified in SECRETS_JSON
5. Build the report from a combination of patient and match data
   1. `$ ctm-report --pts patient-normalized.json --matches <OUTPUT-FROM-export_matches.py.JSON> --trials trials-all-lmm-edited.json --sample-id 8 --out sample8.pdf`

---

### MongoDB Compass Views

Each daily database gets its own set of standard views for exploring trials and matches in Compass. Views live per-database, so recreate them against each new dated DB (idempotent — safe to re-run):

```bash
mongosh "mongodb://localhost:27018/<dated_db>" scripts/create_default_views.js
```

**Views on `trial_match`** (one row per patient↔trial match reason):

| View | What it shows |
|---|---|
| `genomic_matches` | All genomic-driven matches (`reason_type: "genomic"`) — every gene/variant/CNV/fusion/MMR/signature hit. |
| `clinical_matches` | Clinical matches where `match_type: "generic_clinical"` (diagnosis/age/gender/ECOG; excludes TMB). |
| `specific_ct_matches` | Matches where `cancer_type_match: "specific"`. ⚠️ This flag is set by default and shows up even on age-only trials — not a reliable "meaningful" filter on its own. |
| `top_matched` | Strongest matches: `cancer_type_match: "specific"` **and** `match_type: "gene"`. |
| `match_type_breakdown` | Aggregation: match count + distinct-trial count per `match_type`, sorted. At-a-glance distribution. |
| `mt_<value>` | One auto-generated view per distinct `match_type` present (e.g. `mt_gene`, `mt_generic_clinical`, `mt_variant`, `mt_tmb`, `mt_mmr`). |
| `trial_match_meaningful` | ⭐ Matches only for trials with a **specific diagnosis or genomic** criterion. Excludes age-only, empty, and `_SOLID_`/`_LIQUID_` trials — the noise-filtered match list. |
| `trial_match_generic_oncotree` | Matches for `_SOLID_`/`_LIQUID_` basket trials, kept in their own bucket. |

**Views on `trial`** (one row per trial document):

| View | What it shows |
|---|---|
| `biomarker_trials` | Trials with ≥1 entry in `_llm_curation.biomarker_references`. |
| `ct_matches` | Full trial docs for every trial that produced ≥1 match. **Not deduped** — reflects duplicate trial copies. |
| `uniq_matches_full` | ⭐ Full trial docs for the unique **meaningful**-matched trials, **deduped** (one doc per trial). The "actually-relevant trials" list. |
| `trial_no_match_clause` | Trials with an empty `match[]` — no criteria at all. |
| `trial_trivial_criteria` | Trials with only age/gender/ECOG — no diagnosis, no genomic. |
| `trial_basket_solid_liquid` | Trials whose diagnosis is `_SOLID_`/`_LIQUID_` (broad basket). |

**Two mental groupings:**

- **"Show me the good matches"** → `uniq_matches_full` (deduped trial docs) or `trial_match_meaningful` (per-match-reason rows).
- **"Show me what needs curation"** → `trial_no_match_clause` + `trial_trivial_criteria` (trials whose eligibility was never encoded past age).

**Tier logic** — each trial lands in exactly one tier, decided by inspecting its `step[0].match`:

- **meaningful** — has a genomic clause **or** a specific OncoTree diagnosis (not `_SOLID_`/`_LIQUID_`)
- **basket** — has an OncoTree diagnosis but only `_SOLID_`/`_LIQUID_`
- **trivial** — non-empty match, but no diagnosis and no genomic (age/gender/ECOG only)
- **empty** — `match[]` is empty

> [!NOTE]
> The tier-based views (`trial_match_meaningful`, `uniq_matches_full`, and the three `trial_*` classification views) bake in trial-ID lists computed at script run time, because MongoDB views can't recursively detect a diagnosis/genomic clause nested inside `and`/`or` wrappers. If you load more trials into a DB *after* running the script, re-run it to reclassify. The plain aggregation views (`match_type_breakdown`, `biomarker_trials`, etc.) always reflect live data.

---

## Schemas

**Schema levels:**
- `schemas/raw/` - Source-faithful models: one per data source (PT Excel sheets, AMC XML, Sparrow XLSX, West XLSX, and CTGov API). Fields mirror source column names exactly.
- `schemas/matchminer/` - MatchMiner-ready models.
  - Patient data (`MMClinical`, `MMGenomic`)
  - Normalized trial data (`ClinicalTrialNormalized`. Trial documents require manual curation of the match tree before loading into MatchMiner.
  - Trial match data (`MMTrialMatchExport`). This is the output generated after MatchMiner matches a patient to a trial and the data is exported out of MongoDB.

## Normalized Trials and CTML Curation

`ctm-mm trials` outputs a list of `ClinicalTrialNormalized` documents (schema: `src/ctm/schemas/matchminer/clinical_trial.py`). Each document has the following top-level keys:

| Key              | Description                                                                                                                                                    |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `protocol_no`    | Internal protocol number. Populated for AMC trials only; null for CTGov-sourced trials unless set manually.                                                    |
| `nct_id`         | ClinicalTrials.gov identifier.                                                                                                                                 |
| `status`         | Normalized status string: `open to accrual`, `closed to accrual`, or `suspended`.                                                                              |
| `entity`         | Source tag: `amc`, `ctgov`, `sparrow`, or `west`.                                                                                                              |
| `treatment_list` | CTML match tree. Contains `step → arm → match` nodes. This is what MatchMiner queries against.                                                                 |
| `eligibility`    | Parsed inclusion/exclusion criteria with full nesting preserved. For reference only — not automatically wired into the match tree.                             |
| `_summary`       | Display-friendly summary fields: phase, age group, sponsor, drugs, conditions, PI, etc.                                                                        |
| `_raw`           | Complete source dump for zero data loss. Pattern B sources (Sparrow, West) also include a `_raw._sparrow` or `_raw._west` sub-key with the original Excel row. |

### Integration patterns

- **Pattern A (AMC):** XML → raw schema → `ClinicalTrialNormalized` directly. No CTGov lookup.
- **Pattern B (Sparrow, West):** Excel provides NCT IDs → each trial is fetched from CTGov → normalized via the CTGov pipeline → source metadata merged into `_raw`.

### What is auto-populated vs. what needs manual curation

`ctm-mm trials` gets you most of the way there, but the match tree requires manual work before MatchMiner can use it:

- `treatment_list.step[0].match` is seeded with age constraints only (AMC), or age + gender for sex-restricted CTGov trials
- All genomic criteria (Hugo symbol, protein change, variant category, etc.) must be added manually as `genomic` match nodes
- All oncotree/cancer type criteria must be added manually as `clinical` match nodes
- Eligibility free text is parsed and stored in `eligibility` as a reference, but is **not** automatically converted into match nodes
- AMC trials have no arm structure — a single stub arm (`ARM 1`) is generated; multi-arm or dose-escalation trials must be expanded manually

> [!NOTE]
> An empty `match: []` array fails closed, not open — it matches **zero** patients, not every patient. MatchMiner only records a patient as matched when an actual clinical/genomic query runs and returns a reason for that patient; with no criteria in the match node, no query runs and no reasons are ever recorded, so the trial gets no matches until curation adds real criteria. So a stub arm/step you haven't gotten to yet is safe by default — it just won't show up in results, rather than accidentally matching everyone.

### Loading into MatchMiner

Once the match tree is curated, load the trial documents:

```bash
python -m matchengine.main load -t trials.json --trial-format json --db <your-db>
```

MatchMiner expects each document to conform to CTML format. See the [MatchMiner docs](https://matchminer.gitbook.io) for the full field reference.

## Setup

### Local installation (recommended)

See below. A virtualenv plus an existing MongoDB is the simplest setup, and it is
how the toolkit is currently run.

### Docker

Optional. Useful if you want MongoDB provided for you, or a Linux environment
matching the server. Docker Compose runs the app plus MongoDB:

```bash
# Build and start app + MongoDB
docker-compose up --build

# Open a shell in the app container
docker-compose exec app bash

# Run a command directly
docker-compose exec app ctm-mm --help

# Stop containers (data persists)
docker-compose down

# Stop and wipe MongoDB data
docker-compose down -v
```

> [!NOTE]
> The compose file bind-mounts the repo at `/app`, but the package is installed
> non-editable, so **editing source will not take effect without a rebuild**. If
> you want live code changes in the container, change the Dockerfile to
> `pip install -e ".[all,dev]"`.

MongoDB data is stored in a named Docker volume (`ctm-report-preview_mongo_data`) and survives container restarts. To inspect it:

```bash
docker volume ls
docker volume inspect ctm-report-preview_mongo_data

# Open a mongo shell
docker-compose exec mongo mongosh
```

### Local installation

Install from this checkout, not from PyPI — the published `ctm-toolkit` is well
behind and does not include the packaged report templates:

```bash
uv venv
uv pip install -e ".[all,dev]"                                   # report + preview + llm + pytest/ruff
uv pip install "git+https://github.com/wintermutant/matchengine-V2"
```

Extras, if you want a narrower install: `[report]` (PDF), `[preview]` (live
reload), `[llm]` (UMGPT curation), `[dev]` (pytest + ruff), `[all]`.

WeasyPrint needs native Pango. On macOS:

```bash
brew install pango
```

On Debian/Ubuntu these two are sufficient — the `-dev` headers are not needed
because WeasyPrint and Pillow ship prebuilt wheels:

```bash
sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0
```

### Running tests

```bash
pytest          # 137 tests, fully offline — no network access at any point
ruff check .    # lint
```

The suite never reaches the network. An autouse fixture in `tests/conftest.py`
blocks `urllib.request.urlopen`, and the West/Sparrow transformers — which
normally fetch trial content from ClinicalTrials.gov — are pointed at canned
responses in `tests/fixtures/clinicaltrial_gov/`.

### Disclaimer about MatchMiner and MongoDB

[Matchminer](https://matchminer.gitbook.io/matchminer/matchengine-v2/introduction) uses [MongoDB](https://www.mongodb.com/) to faciliate patient-trial matches. In a nutshell, it stores all patient data in 2 collections: i) clinical and ii) genomic. It stores all clinical trials in the *trial* collection. When you run `$ python -m matchengine.main load ...`, it stores all the patient and trial data in these collection.

When we run `$ python -m Matcher.main ...`, this connects to the database and matches all clinical+genomic docs for each patient with eligible trials in the trial collection and saves the results in the *trial_match* collection.

**We need to connect to MongoDB** to run Matchminer. This requires us to provide a username, password, and connection url to Mongo. This info is stored in a file called SECRETS_JSON.json. We also need to ensure we have Mongo installed with an instance running for us to make this work. See our forked [MatchMiner README](https://github.com/wintermutant/matchengine-V2#mongo-setup) for more info.


## Build a report

There is exactly one way to build a report: from a patient collection, a
trial collection, and a flat `trial_match` collection, for one `--sample-id`
at a time. To build a PDF report from the versioned test fixtures:

```bash
$ ctm-report \
    --pts tests/fixtures/test-pts-v0.0.1.json \
    --trials tests/fixtures/test-trials-v0.0.1.json \
    --matches tests/fixtures/test-matches-v0.0.1.json \
    --sample-id 8 \
    --out output.pdf
```

`--pts` follows the same format output by `ctm-mm patients <xlsx> --out pts.json`
— MatchMiner-normalized JSON with 3 top-level keys: i) `clinical` ii) `genomic`
iii) `extras`. The report only ever reads `extras.patients[SAMPLE_ID]` for
name/MRN/entity/metastasis-site fields; everything else comes from the trial
and match collections.

`--trials` is the output of `ctm-mm trials` (see `ClinicalTrialNormalized` in
*src/ctm/schemas/matchminer/clinical_trial.py*) — a flat list of trial
documents, keyed by `protocol_no`.

`--matches` is a flat list of `trial_match` documents (the real MatchMiner
`trial_match` Mongo collection, or an export of it) — one entry per
patient/trial/reason combination, filtered internally to the given
`--sample-id`. The primary match is selected by preferring `match_level: arm`
over `step`, then `reason_type: genomic` over `clinical`, then `sort_order`.
All other trials referenced by that patient's matches appear as secondary
matches.

For a live preview in your browser:

```bash
$ ctm-report \
    --pts tests/fixtures/test-pts-v0.0.1.json \
    --trials tests/fixtures/test-trials-v0.0.1.json \
    --matches tests/fixtures/test-matches-v0.0.1.json \
    --sample-id 8 \
    --preview
```

This opens a browser tab at `http://localhost:5500/report.html`. Any edit to a
template in `src/ctm/templates/`, the stylesheet in `src/ctm/static/report.css`,
or any of the three input files automatically re-renders the report and refreshes
the page. Output is written to `./output/` relative to where you run the command.


# Project layout

## Folders

- `src/ctm/` — the package. Everything the installed wheel needs lives here.
  - `templates/` — `report.html` is the base page, `_*.html` are the per-section includes
  - `static/report.css` — shared styling, including the `@page` rule for PDF page size/margins
  - `content/methods.json` — the Methods prose rendered into every report
  - `refs/` — gene/variant knowledge base used by `ctm-llm biomarkers`
  - `paths.py` — resolves packaged reference data and the per-user cache directory
  - `reports/builder.py` — loads JSON data and renders the Jinja2 template to HTML
  - `report_cli.py` — the `ctm-report` CLI; builds a PDF or serves a live-reload preview
- `tests/fixtures/` — all mock data (see below)
- `scripts/` — `make_template.py` writes a blank intake workbook;
  `create_default_views.js` creates the Compass views

> [!IMPORTANT]
> Report templates, CSS, and reference data live **inside** `src/ctm/` so an
> installed wheel can render without a source checkout. Anchor new asset paths to
> the package (see `paths.py`), never to the repo root —
> `tests/test_packaging.py` fails if that regresses.

## Mock data

All example data lives in `tests/fixtures/`. The repo contains **no real patient
or trial data** — names, MRNs, protocol numbers, and NCT IDs are all fabricated.

| Fixture | Purpose |
| --- | --- |
| `test-pt-data-v0.0.1.xlsx` | Reference intake workbook: mock patients plus a sample row for every findings sheet. Doubles as the layout to copy when filling one in. |
| `test-pts-v0.0.1.json` | Normalized patient collection — output of `ctm-mm patients` |
| `test-trials-v0.0.1.json` | Fully curated trial collection, i.e. post-LLM and post-manual-review. This is what report tests need, since genomic and OncoTree criteria only exist after curation. |
| `test-matches-v0.0.1.json` | `trial_match` collection, joined to the trials fixture by `protocol_no` |
| `test-trials-{amc,sparrow,west}-v0.0.1.*` | Raw per-source samples, for normalization tests only |
| `clinicaltrial_gov/NCT*.json` | Canned ClinicalTrials.gov API responses, trimmed to the fields the transformers read, so tests run offline |


# Extensions

MatchMiner's matching fields are config-driven, making it straightforward to add new matchable fields without touching the core engine. The config lives at `matchengine/config/dfci_config.json` in the matchengine-V2 repo.

## Adding a new genomic field

To add a boolean or freeform string field, add an entry to `ctml_collection_mappings.genomic.trial_key_mappings`, add the field to `projections.genomic`, and add it to `indices.genomic`. Use `"nomap"` when the trial and patient values should match exactly with no transformation.

**Example — boolean field:**
```json
"ALIEN_MUTATION": {
  "sample_key": "ALIEN_MUTATION",
  "sample_value": "nomap"
}
```

A trial requiring this field would include in its match node:
```json
{ "genomic": { "alien_mutation": true } }
```

And the patient's genomic document would need:
```json
{ "ALIEN_MUTATION": true }
```

`nomap` works equally well for freeform strings — the trial value and patient value must simply match exactly. If you control both sides (i.e. you populate the patient data and curate the trial CTML), consistency is easy to maintain.

## Adding a new clinical field

Same pattern, but under `ctml_collection_mappings.clinical.trial_key_mappings`. MatchMiner will query the `clinical` collection instead of `genomic`. Also add the field to `MMClinical` in `src/ctm/schemas/matchminer/patient.py` and populate it from the patient Excel template.

**Example — patient stage:**
```json
"STAGE": {
  "sample_key": "STAGE",
  "sample_value": "nomap"
}
```

## Adding a field with a custom value transform

When source data uses inconsistent representations of the same value, use a named transform function instead of `"nomap"`. The function is defined in `matchengine/plugins/DFCIQueryTransformers.py` and translates the trial's criterion value into the MongoDB query before matching.

**Example — `REVERSE_KEY` field that reverses a string before matching:**

In `dfci_config.json`:
```json
"REVERSE_KEY": {
  "sample_key": "REVERSE_KEY",
  "sample_value": "reverse_map"
}
```

In `DFCIQueryTransformers.py`, add a method to the transformers class:
```python
def reverse_map(self, sample_value, trial_value):
    return {"REVERSE_KEY": trial_value[::-1]}
```

A trial with `"reverse_key": "olleh"` would then match a patient whose `REVERSE_KEY` field is `"hello"`. In practice, transforms are used to normalize value variants — e.g. `"MSI-High"`, `"MSI_H"`, and `"MSI-H"` all resolving to the same canonical match — rather than to reverse strings, but the mechanism is identical.

## How patient data enters the match

Patient data never flows through the Python transform layer. The actual match works in three steps:

1. **Transform function** — receives only the trial's criterion value and returns a MongoDB query object, e.g. `{ MMR_STATUS: "Deficient (MMR-D / MSI-H)" }`
2. **Engine** — appends a `$in: [clinical_ids]` filter to that query and fires it directly at MongoDB
3. **MongoDB** — executes the comparison against stored patient documents and returns matching IDs

The transform function has no knowledge of any specific patient. It only translates trial language into MongoDB query syntax. The patient document is a MongoDB concern exclusively.

## Current limitations of the transform layer

Because transform functions only receive the trial's criterion value and never see the patient document, any eligibility criterion whose threshold depends on other patient fields cannot be expressed in CTML and must be flagged for manual clinician review.

**Examples of criteria that cannot be automated:**

- *Serum creatinine by age/gender table* — the acceptable creatinine threshold varies by both age and gender. Evaluating this requires knowing the patient's age and gender at query time, which the transform function cannot access.
- *Lab value ≤ 1.5x ULN* — the upper limit of normal (ULN) is age-dependent, so `1.5x ULN` is not a fixed number. A fixed approximation (e.g. adult ULN) will be wrong for pediatric patients.

The general pattern: any criterion of the form *"threshold depends on [other patient attribute]"* falls outside what the plugin layer can handle without custom engine-level code. These criteria are preserved in `eligibility.inclusion` and `eligibility.exclusion` for human review, which is why that field is kept even though it is not wired into the match tree.

**Exception — age/gender-conditional thresholds can be expressed as a large `or` block:**

Rather than requiring a custom transform, each row of a lookup table (e.g. serum creatinine by age and gender) becomes one `and` branch inside an `or`. No new engine code is needed — only a new clinical field (e.g. `CREATININE`) with a range transform, and verbose but correct CTML curation:

```json
{
  "match": [
    {
      "or": [
        {
          "and": [
            { "clinical": { "age_numerical": ">=1", "age_numerical": "<6" } },
            { "clinical": { "creatinine": "<=0.8" } }
          ]
        },
        {
          "and": [
            { "clinical": { "age_numerical": ">=6", "age_numerical": "<10" } },
            { "clinical": { "creatinine": "<=1.0" } }
          ]
        },
        {
          "and": [
            { "clinical": { "age_numerical": ">=16", "gender": "Male" } },
            { "clinical": { "creatinine": "<=1.7" } }
          ]
        }
      ]
    }
  ]
}
```

8 age bands × 2 genders = up to 16 `and` branches, but it is fully correct and requires no engine changes — just verbose curation.

# TODO

As it stands, the pipeline has multiple manual processing steps. Ideally, we can automatically integrate from the raw data sources and take it all the way to matching and report building.
