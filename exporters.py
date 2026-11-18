from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document


def _records(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for result in results for record in result.get("records", [])]


def _data_frame(results: list[dict[str, Any]]) -> pd.DataFrame:
    records = _records(results)
    if records:
        exported = []
        for record in records:
            exported.append(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict, tuple))
                    else value
                    for key, value in record.items()
                }
            )
        return pd.DataFrame(exported)
    return pd.DataFrame(columns=["_file", "status", "error"])


def _sources_frame(results: list[dict[str, Any]]) -> pd.DataFrame:
    sources = []
    for result in results:
        records = result.get("records", [])
        if records:
            for record in records:
                sources.append(
                    {
                        "file": record.get("_file", result.get("file")),
                        "page": record.get("_page"),
                        "model": record.get("_model"),
                        "status": result.get("status"),
                    }
                )
        else:
            sources.append(
                {
                    "file": result.get("file"),
                    "page": None,
                    "model": None,
                    "status": result.get("status"),
                }
            )
    return pd.DataFrame(sources, columns=["file", "page", "model", "status"])


def write_json(results: Any, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_xlsx(results: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _data_frame(results).to_excel(writer, sheet_name="Data", index=False)
        _sources_frame(results).to_excel(writer, sheet_name="Sources", index=False)
    return path


def write_csv(results: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _data_frame(results).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_docx(results: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading("SI Document Extractor Results", level=0)
    presets_path = Path(__file__).with_name("presets.json")
    presets = json.loads(presets_path.read_text(encoding="utf-8"))
    for result in results:
        document.add_heading(str(result.get("file", "Document")), level=1)
        records = result.get("records", [])
        if not records:
            document.add_paragraph(f"Status: {result.get('status', 'failed')}")
            if result.get("error"):
                document.add_paragraph(f"Error: {result['error']}")
            continue
        preset = presets.get(result.get("preset"), {})
        if preset.get("mode") == "multi_row":
            columns = list(
                dict.fromkeys(
                    key
                    for record in records
                    for key in record
                    if key not in {"_file", "_page", "_model"}
                )
            )
            if not columns:
                document.add_paragraph("No row fields were extracted.")
                continue
            table = document.add_table(rows=1, cols=len(columns))
            for index, column in enumerate(columns):
                table.rows[0].cells[index].text = column
            for record in records:
                cells = table.add_row().cells
                for index, column in enumerate(columns):
                    value = record.get(column)
                    cells[index].text = (
                        json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (list, dict, tuple))
                        else "" if value is None else str(value)
                    )
            continue
        for record in records:
            for key, value in record.items():
                document.add_paragraph(f"{key}: {value if value is not None else ''}")
    document.save(path)
    return path


def write_md(results: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = _data_frame(results)
    columns = [str(column) for column in frame.columns]
    rows = []
    for row in frame.itertuples(index=False, name=None):
        rows.append(
            [
                "" if value is None or (not isinstance(value, (list, dict, tuple)) and pd.isna(value))
                else str(value).replace("|", r"\|")
                for value in row
            ]
        )
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
