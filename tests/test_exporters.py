import json

import pandas as pd
from docx import Document
from openpyxl import load_workbook

from exporters import write_csv, write_docx, write_json, write_md, write_xlsx


def sample_results():
    return [
        {
            "file": "certificate.pdf",
            "preset": "certificate",
            "status": "ok",
            "records": [
                {
                    "certificate_name": "Python Basics",
                    "recipient_name": "Ada Lovelace",
                    "issuing_organization": "Issuer | Lab",
                    "skills": ["Python", "Data"],
                    "_file": "certificate.pdf",
                    "_page": 2,
                    "_model": "model-a",
                }
            ],
            "error": None,
        },
        {
            "file": "broken.pdf",
            "preset": "certificate",
            "status": "failed",
            "records": [],
            "error": "No readable page",
        },
    ]


def test_xlsx_export_reopens_with_data_and_sources_sheets(tmp_path):
    path = write_xlsx(sample_results(), tmp_path / "results.xlsx")

    workbook = load_workbook(path, data_only=True)
    assert workbook.sheetnames == ["Data", "Sources"]
    data = list(workbook["Data"].values)
    sources = list(workbook["Sources"].values)
    data_record = dict(zip(data[0], data[1]))
    assert data[0][0:3] == ("certificate_name", "recipient_name", "issuing_organization")
    assert data[1][0:3] == ("Python Basics", "Ada Lovelace", "Issuer | Lab")
    assert data_record["skills"] == '["Python", "Data"]'
    assert sources[0] == ("file", "page", "model", "status")
    assert sources[1] == ("certificate.pdf", 2, "model-a", "ok")
    assert sources[2] == ("broken.pdf", None, None, "failed")


def test_csv_export_reopens_with_utf8_bom_and_content(tmp_path):
    path = write_csv(sample_results(), tmp_path / "results.csv")

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    frame = pd.read_csv(path, encoding="utf-8-sig")
    assert frame.loc[0, "recipient_name"] == "Ada Lovelace"
    assert frame.loc[0, "issuing_organization"] == "Issuer | Lab"


def test_xlsx_export_writes_multiple_rows_with_missing_values(tmp_path):
    results = [
        {
            "file": "statement.pdf",
            "preset": "financial",
            "status": "ok",
            "records": [
                {
                    "line_item": "Revenue",
                    "amount": 100,
                    "currency": "USD",
                    "_file": "statement.pdf",
                    "_page": 1,
                    "_model": "model-a",
                },
                {
                    "line_item": "Operating costs",
                    "amount": 30,
                    "_file": "statement.pdf",
                    "_page": 2,
                    "_model": "model-a",
                },
                {
                    "line_item": "Net income",
                    "amount": 70,
                    "currency": "USD",
                    "_file": "statement.pdf",
                    "_page": 3,
                    "_model": "model-a",
                },
            ],
            "error": None,
        }
    ]

    path = write_xlsx(results, tmp_path / "financial.xlsx")

    data = list(load_workbook(path, data_only=True)["Data"].values)
    assert len(data) == 4
    records = {row[0]: dict(zip(data[0], row)) for row in data[1:]}
    assert records["Operating costs"]["amount"] == 30
    assert records["Operating costs"]["currency"] is None


def test_csv_export_writes_multiple_rows_with_missing_values(tmp_path):
    results = [
        {
            "file": "statement.pdf",
            "preset": "financial",
            "status": "ok",
            "records": [
                {"line_item": "Revenue", "amount": 100, "currency": "USD"},
                {"line_item": "Operating costs", "amount": 30},
                {"line_item": "Net income", "amount": 70, "currency": "USD"},
            ],
            "error": None,
        }
    ]

    path = write_csv(results, tmp_path / "financial.csv")

    frame = pd.read_csv(path, encoding="utf-8-sig")
    assert len(frame) == 3
    costs = frame.loc[frame["line_item"] == "Operating costs"].iloc[0]
    assert costs["amount"] == 30
    assert pd.isna(costs["currency"])


def test_docx_export_reopens_with_title_and_key_values(tmp_path):
    path = write_docx(sample_results(), tmp_path / "results.docx")

    document = Document(path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    assert paragraphs[0] == "SI Document Extractor Results"
    assert "certificate_name: Python Basics" in paragraphs
    assert "recipient_name: Ada Lovelace" in paragraphs
    assert "broken.pdf" in paragraphs


def test_docx_export_writes_multi_row_preset_as_table(tmp_path):
    results = [
        {
            "file": "statement.pdf",
            "preset": "financial",
            "status": "ok",
            "records": [
                {
                    "line_item": "Revenue",
                    "amount": 100,
                    "currency": "USD",
                    "_page": 1,
                    "_file": "statement.pdf",
                    "_model": "model-a",
                }
            ],
            "error": None,
        }
    ]

    path = write_docx(results, tmp_path / "financial.docx")

    document = Document(path)
    assert len(document.tables) == 1
    table = document.tables[0]
    assert [cell.text for cell in table.rows[0].cells] == [
        "line_item",
        "amount",
        "currency",
    ]
    assert [cell.text for cell in table.rows[1].cells] == [
        "Revenue",
        "100",
        "USD",
    ]


def test_markdown_export_reopens_with_table_content(tmp_path):
    path = write_md(sample_results(), tmp_path / "results.md")

    text = path.read_text(encoding="utf-8")
    assert "| certificate_name |" in text
    assert "Ada Lovelace" in text
    assert r"Issuer \| Lab" in text


def test_markdown_export_writes_multiple_rows_with_missing_values(tmp_path):
    results = [
        {
            "file": "statement.pdf",
            "preset": "financial",
            "status": "ok",
            "records": [
                {"line_item": "Revenue", "amount": 100, "currency": "USD"},
                {"line_item": "Operating costs", "amount": 30},
                {"line_item": "Net income", "amount": 70, "currency": "USD"},
            ],
            "error": None,
        }
    ]

    path = write_md(results, tmp_path / "financial.md")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert "| Operating costs | 30 |  |" in lines
    assert lines.count("| Revenue | 100 | USD |") == 1
    assert lines.count("| Net income | 70 | USD |") == 1


def test_json_export_reopens_with_full_results(tmp_path):
    results = sample_results()
    path = write_json(results, tmp_path / "results.json")

    assert json.loads(path.read_text(encoding="utf-8")) == results
