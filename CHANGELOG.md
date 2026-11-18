# Changelog

## Post-release live-test fixes

- Made empty-result UI and run-log handling safe, while retaining each file's
  extraction error.
- Added slot environment-variable validation and single-line skips for slots
  without credentials; attempt logs now include the exception message and API
  status/body without logging keys, headers, or prompts.
- Kept valid JSON objects embedded in explanatory model text, and added
  Mistral-compatible Pixtral image payloads with an explicit token limit.
- Set the default vision slots to AliBaba_Qwen (`qwen-vl-max`) and Mistral
  (`pixtral-large-latest`); documented that config contains variable names and
  `.env` contains values.

## Phase 3

- Added financial multi-row, research-paper, and CV presets.
- Added page/character chunking, row de-duplication, and first-non-null
  per-file merging.
- Added table-based DOCX output for multi-row presets.
- Enforced configured file-size limits and surfaced long-PDF warnings.
- Captured model failures, fallbacks, and extraction errors in each run log.
- Documented installation, configuration, architecture, and extension points.

## Phase 3 Refix

- Applied configured PDF render DPI, defaulting to 150, and documented the setting.
- Made `_page` the financial preset's only source-page field.
- Updated Streamlit editor width settings and pytest-asyncio fixture loop scope.
- Added multi-row sparse-data coverage for XLSX, CSV, and Markdown exports.
