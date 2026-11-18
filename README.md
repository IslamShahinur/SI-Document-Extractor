# SI Document Extractor

A local Streamlit app that extracts structured data from PDFs, Word documents,
text, spreadsheets, and images using configurable OpenAI-compatible model slots.

## Install

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Configure models

Copy `.env.example` to `.env` and set the base URL and API key for the providers
configured in `config.yaml`. Keep `.env` private; it is git-ignored.

### Slot setup

`config.yaml` contains environment-variable names only (for example,
`PRIMARY_BASE_URL` and `PRIMARY_API_KEY`); `.env` holds their actual URL and key
values. Never put URLs or API keys in `config.yaml`. The default vision slots
are AliBaba_Qwen (`qwen-vl-max`) and Mistral (`pixtral-large-latest`). Slots
with missing credentials are skipped and logged once.

## Run

```powershell
streamlit run app.py
```

Upload documents, choose a preset, edit the extraction command if needed, select
export formats, and click **Extract**. The presets cover certificates, financial
statements, research papers, CVs, and custom commands. Enable **Local only** to
restrict requests to slots marked `local: true` in `config.yaml`. Results and
`run.log` are saved under `output/`.

The default per-file limit is 25 MB. PDFs over the configured 200-page warning
threshold can still be processed; long documents are chunked. Adjust the limits
and chunk sizes in `config.yaml`.

## Tests

```powershell
python -m pytest -q
```
