from io import BytesIO

import pymupdf as fitz
import pandas as pd
import pytest
from docx import Document
from PIL import Image

from readers import read


@pytest.mark.parametrize("extension", [".md", ".txt"])
def test_reads_plain_text_formats(tmp_path, extension):
    source = tmp_path / f"notes{extension}"
    source.write_text("A document body.", encoding="utf-8")

    result = read(source)

    assert result.kind == extension[1:]
    assert result.pages == [{"n": 1, "text": "A document body."}]
    assert result.images == []


def test_reads_pdf_text_and_renders_short_pages(tmp_path):
    source = tmp_path / "sample.pdf"
    with fitz.open() as pdf:
        digital = pdf.new_page()
        digital.insert_text((72, 72), "This is a full digital page with enough text.")
        scanned = pdf.new_page()
        scanned.insert_text((72, 72), "short")
        pdf.save(source)

    result = read(source)

    assert result.kind == "pdf"
    assert len(result.pages) == 2
    assert "full digital page" in result.pages[0]["text"]
    assert result.pages[1]["text"].strip() == "short"
    assert result.scanned is True
    assert result.images[0]["n"] == 2
    assert result.images[0]["png_bytes"].startswith(b"\x89PNG")


def test_configured_dpi_changes_pdf_render_scale(tmp_path):
    source = tmp_path / "scanned.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(source)

    default = read(source, config_path=tmp_path / "missing-config.yaml")
    config = tmp_path / "config.yaml"
    config.write_text("dpi: 300\n", encoding="utf-8")
    custom = read(source, config_path=config)
    with Image.open(BytesIO(default.images[0]["png_bytes"])) as default_render:
        default_size = default_render.size
    with Image.open(BytesIO(custom.images[0]["png_bytes"])) as custom_render:
        custom_size = custom_render.size

    assert abs(custom_size[0] - default_size[0] * 2) <= 1
    assert abs(custom_size[1] - default_size[1] * 2) <= 1


def test_reads_docx_paragraphs_and_tables(tmp_path):
    source = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("Certificate")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Name"
    table.rows[0].cells[1].text = "Ada"
    document.save(source)

    result = read(source)

    assert result.kind == "docx"
    assert "Certificate" in result.pages[0]["text"]
    assert "Name | Ada" in result.pages[0]["text"]


@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
def test_reads_spreadsheet_formats_as_text(tmp_path, extension):
    source = tmp_path / f"table{extension}"
    frame = pd.DataFrame([{"name": "Ada", "score": 10}])
    if extension == ".csv":
        frame.to_csv(source, index=False)
    else:
        frame.to_excel(source, index=False)

    result = read(source)

    assert result.kind == extension[1:]
    assert "Ada" in result.pages[0]["text"]
    assert "score" in result.pages[0]["text"]


@pytest.mark.parametrize("extension", [".png", ".jpg", ".jpeg", ".webp"])
def test_reads_image_formats_as_png_bytes(tmp_path, extension):
    source = tmp_path / f"sample{extension}"
    image = Image.new("RGB", (4, 4), "white")
    image.save(source, format={".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(extension, "PNG"))

    result = read(source)

    assert result.kind == "image"
    assert result.pages == []
    assert result.scanned is True
    assert result.images[0]["n"] == 1
    assert result.images[0]["png_bytes"].startswith(b"\x89PNG")


def test_rejects_missing_and_unsupported_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        read(tmp_path / "missing.txt")
    unsupported = tmp_path / "source.bin"
    unsupported.write_bytes(b"content")
    with pytest.raises(ValueError, match="Unsupported file type"):
        read(unsupported)


def test_enforces_configured_file_size_limit(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("too large", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("max_file_mb: 0.000001\n", encoding="utf-8")

    with pytest.raises(ValueError, match="configured maximum"):
        read(source, config_path=config)


def test_warns_when_pdf_exceeds_configured_page_threshold(tmp_path, caplog):
    source = tmp_path / "sample.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(source)
    config = tmp_path / "config.yaml"
    config.write_text("warn_pages: 1\n", encoding="utf-8")

    read(source, config_path=config)

    assert "page_limit_warning" in caplog.text
