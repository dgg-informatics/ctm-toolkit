# Michigan Medicine Clinical Trial Integration Toolkit ⚒️

This repo prepares data from various sources to integrate with popular open-source clinical trial matching software (MatchMiner supported, TrialMatchAI at some point). Beyond providing input sources for popular clinical trial matching software, this repo also provides post-matching processing scripts to generate reports. For instance, once clinical trial matching has completed, it interfaces with the structured output (from MatchMiner) to provide a report for the patient.

## Quick Start

```bash
# Step 1: Normalize patient data from Excel template
ctm-mm patients <patient_data_template.xlsx> --pt-uuid 1234 --out pt_1234.json

# Step 2: Normalize trial data from AMC XML or ClinicalTrials.gov
ctm-mm trials --amc <amc_trials.xml> --sparrow <sparrow.xlsx> --west <west.xlsx> --out trials.json

# Note - We can also fetch trials directly from ClinicalTrials.gov without any template:
# Step 2 (alternate): Fetch a single trial from ClinicalTrials.gov (raw or normalized)
ctm-fetch --nct NCT03067181 --output nct-raw.json
# Step 2.5 (alternate): Normalize clinicaltrials.gov data to matchminer (mm)
ctm-fetch --nct NCT03067181 --output nct-normalized.json --fmt-mm

# Step 3
# Ensure you do the MANUAL PROCESSING from the normalized format to MATCHMINER!!!
# This involves translating text eligiblity criteria into MatcherMiner format

# Step 4: Load trial data into MatchMiner
python -m matchengine.main load -t trials.json --trial-format json --db test

# Step 5: Run MatchMiner
python -m Matcher.main --config source/Matcher/config/config.json

# Step 6: Export match results from MatchMiner's Mongo database
SECRETS_JSON=SECRETS_JSON.json python export_matches.py --patient 7439568 --output export/ --db v1

# Step 7: Build report from match results
ctm-report --pt data/patient.json --matches data/matchminer_export.json --engine mm --out output.pdf
```

### Warning for Patient and Clinical Trial Data

#### Patient Data

Patient data is based upon **manual recording** of patient data into a template excel sheet. Until we can automate the process of pulling a patient's file and normalizing it, this will have to do. See example at */data/raw/pt_template.xlsx*.

After you fill out the template excel sheet, please be careful of:

- ONCOTREE_PRIMARY_DIAGNOSIS_NAME passes through directly from the Excel oncotree_primary_diagnosis column - if that cell has a free-text diagnosis instead of an Oncotree code, MatchMiner won't match on it correctly
- TRUE_HUGO_SYMBOL passes through from the gene column as-is - no validation against HGNC so be careful to use a HUGO symbol
- variant_type in the Excel drives VARIANT_CATEGORY mapping - if someone enters an unrecognized value it gets skipped with a warning

#### Clinical Trial Data

Clinical trial data comes from automated sources for AMC and Sparrow. For AMC, an export of the OnCORE database is retreived in the form of an XML file (see *data/raw/amc_trails_raw.xml*). For Sparrow, an Excel document is emailed each month (see *data/sparrow_trials_raw.xlsx*).

For UMH-West, an Excel sheet is emailed is a format that is incomplete and requires manual processing immediately. Thus, this repo holds a template for recording West trial data instead of the raw data file we receive. See the template for West's trial data at *data/raw/west_trial_template.xlsx* --> note the *_template.xlsx instead of *_raw.xlsx ;-)

The automation from AMC, Sparrow, West, and ClinicalTrials.gov clinical trial data requires some manual massaging of the data before it can be put into MatchMiner:

- treatment_list.step[0].match is auto-populated with age constraints only (AMC) or age + gender for sex-restricted trials (CTGov) - all genomic and oncotree criteria must be filled in manually
- Eligibility criteria are parsed and preserved structurally (inclusion/exclusion with nesting) but are not converted into MatchMiner and/or match nodes automatically
- AMC trials have no arm structure - a single stub arm (ARM 1) is generated; if the trial has multiple arms or dose cohorts they must be added manually
- status is normalized automatically (OPEN TO ACCRUAL → open to accrual) but verify any trial with an unusual status value, as unrecognized values pass through unchanged
- AMC data has no drug/intervention field - _summary.drugs will always be empty and must be populated manually if needed for display
- protocol_no is only populated for AMC trials; CTGov trials will have null there unless manually set


# Background

Creating a clinical trial match involves two key categories of data:
1. patient information (query)
2. clinical trial information (reference)

Patient data must match up to trial eligibility requirements to make a match. Trial eligibility often relies on some combination of patient demographics, diagnoses, prior treatments, as well as molecular and genetic data. Combining all of this information into one spot can be difficult, thus the purpose of this toolkit is to make it easier to combine all the data sources needed into the formats required for popular clinical trial engines and report building.

For matching a patient to trials, we define 2 categories of patient data:
1. clinical data
2. genomic data

**Clinical data** is kind of an overloaded term, but it refers to general patient demographics as well as diagnoses. Examples include Age, Sex, Primary Diagnosis, Weight, etc. **Genomic data** comes from molecular testing, which is either performed in-house (AMC's Division of Diagnostic Genetics and Genomics) or from an external company (Tempus, Caris, Foundation, to name a few).


## Data pipeline

1. Raw --> Normalized Patient Data
   1. Fill out patient_data_template.xlsx
   2. Run `$ ctm-mm patients ...`
2. Raw --> Normalized Trial Data. This is much more complex since we have 4 data sources (Sparrow, West, AMC, and ClinicalTrials.gov)
   1. For AMC, you can point to the XML file to create a structure very similar to Matchminer:
      1. `$ ctm-mm trials --amc nct-raw.json --out to-normalized.json`
      2. Above produces a .JSON file that needs a little bit of manual curation to be ingested by MatchMiner
   2. For ClinicalTrials.gov data, you can fetch a particular trial by NCT number and then normalize to the same format as AMC
      1. Run `$ ctm-fetch --nct NCT03067181 --output nct-raw.json --fmt-mm`  # fetch clinical trials and normalize output to matchminer format
      2. You can also run `$ ctm-mm trials --ct <raw-ctgov.json> --out to-normalized.json`  # given a JSON of unnormalized clinicaltrials.gov trials, this will normalize it to matchminer format
      3. Please note 2.1 and 2.2 have overlapping functionality. the --ct <raw-ctgov.json> in 2.2 would come from running `ctm-fetch --nct NCT03067181 --output nct-raw.json`, which omitting the `--fmt-mm`, stored the data in a non-normalized way (raw from ct.gov api).
   3. Ensure you finish **manually curating eligibility criteria** for (1) and (2)
3. **Read MatchMiner docs to prepare for the next 2 steps!**
4. Load in the new data to MatchMiner
   1. `$ python -m matchengine.main load ...`
5. Execute the match!
   1. `$ python -m Matcher.main ...`
6. Export match data from MatchMiner database
   1. `$ python export_matches.py ...`
7. Build the report from a combination of patient and match data
   1. `$ ctm-report ...`


## Schemas

**Schema levels:**
- `schemas/raw/` - Source-faithful models: one per data source (PT Excel sheets, AMC XML, Sparrow XLSX, West XLSX, and CTGov API). Fields mirror source column names exactly.
- `schemas/matchminer/` - MatchMiner-ready models.
  - Patient data (`MMClinical`, `MMGenomic`)
  - Normalized trial data (`ClinicalTrialNormalized`. Trial documents require manual curation of the match tree before loading into MatchMiner.
  - Trial match data (`MMTrialMatchExport`). This is the output generated after MatchMiner matches a patient to a trial and the data is exported out of MongoDB.
- `schemas/trialmatchai/` - Placeholder for future TrialMatchAI integration.

## Setup

### Installation

```bash
uv pip install -r requirements.txt --python .venv/bin/python
```

### Running tests

```bash
.venv/bin/python -m pytest
```

On macOS, WeasyPrint also needs the native Pango library, which isn't installed
by default:

```bash
brew install pango
```

### Disclaimer about MatchMiner and MongoDB

[Matchminer](https://matchminer.gitbook.io/matchminer/matchengine-v2/introduction) uses [MongoDB](https://www.mongodb.com/) to faciliate patient-trial matches. In a nutshell, it stores all patient data in 2 collections: i) clinical and ii) genomic. It stores all clinical trials in the *trial* collection. When you run `$ python -m matchengine.main load ...`, it stores all the patient and trial data in these collection.

When we run `$ python -m Matcher.main ...`, this connects to the database and matches all clinical+genomic docs for each patient with eligible trials in the trial collection and saves the results in the *trial_match* collection.

**We need to connect to MongoDB** to run Matchminer. This requires us to provide a username, password, and connection url to Mongo. This info is stored in a file called SECRETS_JSON.json. We also need to ensure we have Mongo installed with an instance running for us to make this work. See our forked [MatchMiner README](https://github.com/wintermutant/matchengine-V2#mongo-setup) for more info.


## Build a report

To build a PDF report from the mock data:

```bash
$ ctm-report --pt data/mock/pt-data.json --matches data/mock/mm-matches.json --engine mm --out output.pdf
```

The data/mock/pt-data.json file follows the same format that is output from `ctm-mm patients <patient_data_template.xlsx> --out pt-data.json`. This is MatchMiner-normalized data that is in JSON with 3 high-level keys: i) clinical ii) genomic iii) extras. The schema can be found at *src/ctm/schemas/matchminer/patient.py* in `MMClinical` and `MMGenomic`. The report actually only ever touches the *extras* sub-key, since all the rest of the data can come from the MatchMiner match output.

The data/mock/mm-matches.json file follows the schema found in *src/ctm/schemas/matchminer/trial_match.py*, specifically in the model `MMTrialMatchExport`. It is the JSON export produced by MatchMiner after running the matching engine against a patient. It has 3 top-level keys: i) pt_uuid ii) clinical (a minimal patient reference with SAMPLE_ID, MRN, etc.) and iii) trial_match (a list of match records). The primary match is selected by preferring match_level: arm over step, then reason_type: genomic over clinical, then sort_order. All remaining unique trials appear as secondary matches.


For a live preview in your browser:

```bash
$ ctm-report --pt data/mock/pt-data.json --matches data/mock/mm-matches.json --engine mm --preview
```

This opens a browser tab at `http://localhost:5500/report.html`. Any edit to a
template in `templates/`, the stylesheet in `static/report.css`, or the active
data directory (`data/mock/` or `data/real/`, depending on the flag)
automatically re-renders the report and refreshes the page.


# Project layout

## Folders

- `data/` - see Data directories table above
- `templates/` - `report.html` is the base page, `_*.html` are the per-section includes
- `static/report.css` - shared styling, including the `@page` rule for PDF page size/margins
- `src/ctm/reports/builder.py` - loads JSON data and renders the Jinja2 template to HTML
- `preview.py` - live-reload dev server
- `build_pdf.py` - renders the HTML and exports it to PDF via WeasyPrint

## Data directories

We have some example data we store in this repo as a nice reference.

| Directory | Purpose |
|-----------|---------|
| `data/content/` | Static content used to build the report.|
| `data/dump/` | Ignore for now. It's where we dump random raw trial and patient data.|
| `data/mock/` | Synthetic data that shows the format of patient, trial, and match data and is used to build a mock report to show the report format. Patient and trial data are normalized, while match data is exported from the match engine (MatchMiner only for now)|
| `data/raw/` | Templates for manually creating initial patient and clinical trial data. This is the input for the normalization step |


# TODO

As it stands, the pipeline has multiple manual processing steps. Ideally, we can automatically integrate from the raw data sources and take it all the way to matching and report building.

- [ ] make a pipeline that points to raw patient+trial info and spits out match report
- [ ] docker-ize this process
- [ ] deploy this onto PS1A server