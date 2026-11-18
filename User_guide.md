# SI Document Extractor: User Guide

Turn documents (certificates, financial statements, research papers, CVs) into structured data you can export.

## 1. First-time setup

1. Open a terminal in the project folder.
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`.
4. Open `.env` and paste your API keys for the models you want to use. Leave unused ones empty. Never share or commit `.env`.
5. (Optional) Open `config.yaml` to check the model order, upload limits and PDF image quality (`dpi`).

## 2. Start the app

1. In the project folder run: `streamlit run app.py`
2. Your browser opens the app. If not, open the address shown in the terminal.

## 3. Extract data from a document

1. Go to the main extraction view.
2. Upload one or more files (PDF, DOCX, MD and other supported formats).
3. Choose a preset:
   - **certificate**: course and professional certificates
   - **financial**: financial statements (one row per line item)
   - **research**: research papers and reports
   - **cv_data**: CV and career data
   - **custom**: you describe what you want in the command box
4. In the command box, state what output you want, if needed.
5. Start the run.
6. Watch the page warnings and run log. If a model fails, the app retries and then falls back to the next model automatically.

## 4. Check and fix the result

1. Review the preview table.
2. Edit any wrong cell directly in the table. Your edits are saved to the run folder.
3. Columns starting with `_` (`_file`, `_page`, `_model`) show which file, page and model produced each row.

## 5. Export

1. Pick the format: **docx, xlsx, csv, md or json**.
2. Download the file, or download the whole run as a zip.

## 6. Settings and Log

- **Settings**: view and adjust model and run options.
- **Log**: see what happened in past runs, including failures and fallbacks.

## 7. Tips

- Large files: the app warns above the page limit and rejects files above the size limit set in `config.yaml`.
- Sensitive documents: use local-only mode so the file goes only to local models.
- Scanned or image-heavy pages need a vision-capable model in your chain.
- Always check important numbers against the source document.

## 8. Common problems

| Problem | Fix |
|---|---|
| App says no models available | Check your keys in `.env` and the model order in `config.yaml` |
| A file is rejected | It is above the size limit in `config.yaml` |
| Empty or poor result | Try a different preset, or use `custom` with a clearer command |
| Port or install errors | Reinstall with `pip install -r requirements.txt` in a clean virtual environment |
