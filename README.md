# Clinical Trial Match Integration Toolkit

This repo prepares data from various sources to integrate with popular open-source clinical trial matching software (MatchMiner supported, TrialMatchAI at some point). Once clinical trial matching has completed, it interfaces with the structured output (from MatchMiner and TrialMatchAI) to provide a report for the patient. See example workflow:

```bash
# Create MatchMiner-formatted patient data from raw excel format
ctm patients <patient_data_template.xlsx> --pt-uuid 1234 --out pt_1234.json
# Create MatchMiner-formatted trial data from raw yaml formats
ctm trials --sparrow <trials.yaml> --amc <trials.yaml> --west <trials.yaml> --out trials.json
# load the data into MatchMiner's database
python -m matchengine.main load -t path/to/trials/trials.json --trial-format json --db test
# run matchminer
python -m Matcher.main --config source/Matcher/config/config.json
# export MatchMiner data
SECRETS_JSON=SECRETS_JSON.json python export_matches.py --patient 7439568 --output export/ --db v1
# create report from matchminer data 
ctm-report --pt data/patient.json --matches /data/matchminer_export.json --engine mm --out output.pdf
```

# start here / justification

Creating a clinical trial match involves two key categories of data:
1. patient information (query)
2. clinical trial information (reference)

Patient data must match up to trial eligibility requirements to make a match. Trial eligibility often relies on some combination of patient demographics, diagnoses, prior treatments, as well as molecular and genetic data. Combining all of this information into one spot can be difficult and the purpose of this toolkit is to make it easier to combine all the data sources needed into the formats required for popular clinical trial engines and report building.

For matching a patient to trials, we define 2 categories of patient data:
1. clinical data
2. genomic data

Clinical data is kind of an overloaded term, but it refers to general patient demographics as well as diagnoses. Examples include Age, Sex, Primary Diagnosis, Weight, etc. Genomic data comes from molecular testing, which is either performed in-house (AMC's Division of Diagnostic Genetics and Genomics) or from an external company (Tempus, Caris, Foundation, to name a few).


## Data pipeline

1. Raw --> Normalized Patient Data
   1. Fill out patient_data_template.xlsx
   2. Run `$ ctm-mm patients ...`
2. Raw --> Normalized Trial Data
   1. Fill out the yaml file according to templates in `data/raw/*.yaml` for each entity (Sparrow, West, AMC)
   2. Run `$ ctm-mm trials ...`
3. Load in the new data to MatchMiner
   1. `$ python -m matchengine.main load ...`
4. Execute the match!
   1. `$ python -m Matcher.main ...`
5. Export match data from MatchMiner database
   1. `$ python export_matches.py ...`
6. Build the report from a combination of patient and match data
   1. `$ ctm-report ...`


## Schemas

**Schema levels:**
- `schemas/raw/` - engine-agnostic intermediate schema: minimal massaging, covers all
  fields we want to preserve, MongoDB-friendly. This is the stable record.
- `schemas/processed/` - Pydantic models for the fully transformed, engine-ready output. One transformer module per engine.
- `schemas/matchminer` - Schemas specifically for integrating with MatchMiner.
- `schemas/trialmatchai` - Schemas specifically for integrating with TrialMatchAI.

**Why two layers?** Separating normalization from engine-specific transformation means new
engines can be added without re-ingesting source data. The normalized layer is the single
source of truth.

## Setup

### Installation

```bash
uv pip install -r requirements.txt --python .venv/bin/python
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

- `data/` — see Data directories table above
- `templates/` — `report.html` is the base page, `_*.html` are the per-section includes
- `static/report.css` — shared styling, including the `@page` rule for PDF page size/margins
- `src/ctm/reports/builder.py` — loads JSON data and renders the Jinja2 template to HTML
- `preview.py` — live-reload dev server
- `build_pdf.py` — renders the HTML and exports it to PDF via WeasyPrint

## Data directories

We have some example data we store in this repo as a nice reference.

| Directory | Purpose |
|-----------|---------|
| `data/content/` | Static content used to build the report.|
| `data/dump/` | Ignore for now. It's where we dump random raw trial and patient data.|
| `data/mock/` | Synthetic data that shows the format of patient, trial, and match data and is used to build a mock report to show the report format. Patient and trial data are normalized, while match data is exported from the match engine (MatchMiner only for now)|
| `data/raw/` | Templates for manually creating initial patient and clinical trial data. This is the input for the normalization step |


# TODO

As it stands, the pipeline takes 6 steps, with the initial step starting from manually transcribed data from various sources into the i) patient_data_template.xlsx and various <entity>.yaml files. The goal is to incrementally reduce the number of steps. Ideally, we can automatically integrate from the raw data sources and take it all the way to matching and report building.

- [ ] west-trials.yaml has no schema and needs one
- [ ] make a pipeline that points to raw patient+trial info and spits out match report
- [ ] docker-ize this process
- [ ] deploy this onto PS1A server