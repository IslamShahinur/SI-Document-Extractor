from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from chain import ask
from readers import Doc, ImagePage, Page, read


LOGGER = logging.getLogger(__name__)


def _load_presets(presets_path: str | Path = "presets.json") -> dict[str, Any]:
    with Path(presets_path).open(encoding="utf-8") as file:
        return json.load(file)


def _chunks(
    document: Doc, chunk_pages: int, chunk_chars: int = 12000
) -> list[tuple[list[Page], list[ImagePage]]]:
    if chunk_pages < 1:
        raise ValueError("chunk_pages must be a positive integer")
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be a positive integer")
    if not document.pages:
        return [
            ([], document.images[start : start + chunk_pages])
            for start in range(0, len(document.images), chunk_pages)
        ] or [([], [])]

    units: list[Page] = []
    for page in document.pages:
        text = page["text"]
        if len(text) <= chunk_chars:
            units.append(page)
        else:
            units.extend(
                {"n": page["n"], "text": text[start : start + chunk_chars]}
                for start in range(0, len(text), chunk_chars)
            )

    chunks: list[tuple[list[Page], list[ImagePage]]] = []
    current: list[Page] = []
    current_page_numbers: set[int] = set()
    current_chars = 0
    for page in units:
        page_chars = len(page["text"])
        if current and (
            len(current_page_numbers | {page["n"]}) > chunk_pages
            or current_chars + page_chars > chunk_chars
        ):
            images = [
                image for image in document.images if image["n"] in current_page_numbers
            ]
            chunks.append((current, images))
            current = []
            current_page_numbers = set()
            current_chars = 0
        current.append(page)
        current_page_numbers.add(page["n"])
        current_chars += page_chars
    if current:
        images = [
            image for image in document.images if image["n"] in current_page_numbers
        ]
        chunks.append((current, images))
    return chunks


def _build_prompt(preset: dict[str, Any], command: str, pages: list[Page]) -> str:
    fields = preset.get("fields", [])
    field_instructions = "\n".join(
        f'- "{field["key"]}": {field.get("desc", "")}' for field in fields
    )
    text = "\n\n".join(f'[Page {page["n"]}]\n{page["text"]}' for page in pages)
    output_shape = (
        "Return a JSON object with a 'rows' array containing one object per line item. "
        if preset.get("mode") == "multi_row"
        else "Return a JSON object with the requested fields. "
    )
    return (
        f'Preset: {preset["label"]}\n'
        f'Instructions: {preset.get("instructions", "")}\n'
        f"Fields:\n{field_instructions or '(derive relevant fields from the command)'}\n"
        f"User command: {command or '(none)'}\n"
        f"{output_shape}Use null for missing values; do not invent facts. "
        "Include the source page number as _page for each record.\n"
        f"Document text:\n{text}"
    )


def run(
    document: Doc,
    preset_key: str,
    command: str = "",
    local_only: bool = False,
    *,
    presets_path: str | Path = "presets.json",
    config_path: str | Path = "config.yaml",
    ask_fn: Any = None,
) -> dict[str, Any]:
    presets = _load_presets(presets_path)
    if preset_key not in presets:
        raise ValueError(f"Unknown preset: {preset_key}")
    preset = presets[preset_key]
    mode = preset.get("mode")
    if mode not in {"per_file", "multi_row"}:
        raise ValueError(f"Unsupported preset mode: {mode!r}")

    result: dict[str, Any] = {
        "file": document.name,
        "preset": preset_key,
        "status": "failed",
        "records": [],
        "error": None,
    }
    values: dict[str, Any] = {field["key"]: None for field in preset.get("fields", [])}
    first_page = document.pages[0]["n"] if document.pages else 1
    result_page: Any = None
    model: str | None = None
    succeeded = 0
    failures: list[str] = []
    config = {}
    try:
        with Path(config_path).open(encoding="utf-8") as file:
            import yaml

            config = yaml.safe_load(file) or {}
    except FileNotFoundError:
        if ask_fn is None:
            raise
    chunk_pages = config.get("chunk_pages", 6)
    if not isinstance(chunk_pages, int) or chunk_pages < 1:
        raise ValueError("chunk_pages must be a positive integer")
    chunk_chars = config.get("chunk_chars", 12000)
    if not isinstance(chunk_chars, int) or chunk_chars < 1:
        raise ValueError("chunk_chars must be a positive integer")
    ask_model = ask_fn or ask
    rows: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    for pages, images in _chunks(document, chunk_pages, chunk_chars):
        chunk_page = pages[0]["n"] if pages else (images[0]["n"] if images else first_page)
        try:
            response = ask_model(
                _build_prompt(preset, command, pages),
                images=images,
                local_only=local_only,
                config_path=config_path,
            )
            if model is None:
                model = response.get("_model")
            if result_page is None and response.get("_page") is not None:
                result_page = response["_page"]
            if mode == "multi_row":
                response_rows = response.get("rows", response.get("records"))
                if not isinstance(response_rows, list):
                    raise ValueError("Multi-row response must contain a 'rows' array")
                for response_row in response_rows:
                    if not isinstance(response_row, dict):
                        raise ValueError("Each multi-row result must be a JSON object")
                    row = {
                        key: value
                        for key, value in response_row.items()
                        if key not in {"_file", "_model"}
                    }
                    row_page = row.get("_page") or response.get("_page") or chunk_page
                    row["_page"] = row_page
                    if any(f["key"] == "page" for f in preset.get("fields", [])):
                        if row.get("page") is None:
                            row["page"] = row_page
                    row["_file"] = document.name
                    row["_model"] = response.get("_model")
                    content = {
                        key: value
                        for key, value in row.items()
                        if key not in {"_file", "_page", "_model", "page"}
                    }
                    signature = json.dumps(content, ensure_ascii=False, sort_keys=True)
                    if signature not in seen_rows:
                        seen_rows.add(signature)
                        rows.append(row)
            else:
                for key, value in response.items():
                    if key not in {"_model", "_page"} and value is not None:
                        if values.get(key) is None:
                            values[key] = value
            succeeded += 1
        except Exception as error:
            failures.append(str(error))
            LOGGER.exception(
                "extraction_chunk_failed file=%s page=%s error=%s",
                document.name,
                chunk_page,
                error,
            )

    if succeeded:
        if mode == "multi_row":
            result["records"] = rows
        else:
            record = dict(values)
            record.update(
                {
                    "_file": document.name,
                    "_page": result_page if result_page is not None else first_page,
                    "_model": model,
                }
            )
            result["records"] = [record]
        result["status"] = "partial" if failures else "ok"
    if failures:
        result["error"] = "; ".join(failures)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured data from a document")
    parser.add_argument("--file", required=True)
    parser.add_argument("--preset", default="certificate")
    parser.add_argument("--command", default="")
    parser.add_argument("--local-only", action="store_true")
    arguments = parser.parse_args()
    result = run(read(arguments.file), arguments.preset, arguments.command, arguments.local_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
