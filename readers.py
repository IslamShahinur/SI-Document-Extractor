from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from pathlib import Path
from typing import Any, TypedDict

import pymupdf as fitz
import pandas as pd
from docx import Document
from PIL import Image as PILImage
import yaml


LOGGER = logging.getLogger(__name__)


class Page(TypedDict):
    n: int
    text: str


class ImagePage(TypedDict):
    n: int
    png_bytes: bytes


@dataclass
class Doc:
    name: str
    kind: str
    pages: list[Page]
    images: list[ImagePage]
    scanned: bool


def _read_pdf(path: Path, warn_pages: int, dpi: int | float) -> Doc:
    pages: list[Page] = []
    images: list[ImagePage] = []
    with fitz.open(path) as pdf:
        if len(pdf) > warn_pages:
            LOGGER.warning(
                "page_limit_warning file=%s pages=%s warning_threshold=%s",
                path.name,
                len(pdf),
                warn_pages,
            )
        for number, page in enumerate(pdf, start=1):
            text = page.get_text()
            pages.append({"n": number, "text": text})
            if len(text.strip()) < 30:
                scale = dpi / 72
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                images.append({"n": number, "png_bytes": pixmap.tobytes("png")})
    return Doc(path.name, "pdf", pages, images, bool(images))


def _read_docx(path: Path) -> Doc:
    document = Document(path)
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return Doc(path.name, "docx", [{"n": 1, "text": "\n".join(parts)}], [], False)


def _read_table(path: Path) -> Doc:
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        frame = pd.read_excel(path, dtype=str, keep_default_na=False)
    return Doc(
        path.name,
        path.suffix.lower().lstrip("."),
        [{"n": 1, "text": frame.to_string(index=False)}],
        [],
        False,
    )


def _read_image(path: Path) -> Doc:
    with PILImage.open(path) as image:
        output = BytesIO()
        image.save(output, format="PNG")
    return Doc(path.name, "image", [], [{"n": 1, "png_bytes": output.getvalue()}], True)


def _load_limits(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        return {"max_file_mb": 25, "warn_pages": 200, "dpi": 150}
    with path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a YAML mapping")
    return config


def read(file_path: str | Path, *, config_path: str | Path = "config.yaml") -> Doc:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    config = _load_limits(config_path)
    max_file_mb = config.get("max_file_mb", 25)
    if not isinstance(max_file_mb, (int, float)) or max_file_mb <= 0:
        raise ValueError("max_file_mb must be a positive number")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_file_mb:
        raise ValueError(
            f"File is {size_mb:.1f} MB; the configured maximum is {max_file_mb:g} MB."
        )

    extension = path.suffix.lower()
    if extension == ".pdf":
        warn_pages = config.get("warn_pages", 200)
        if not isinstance(warn_pages, int) or warn_pages < 1:
            raise ValueError("warn_pages must be a positive integer")
        dpi = config.get("dpi", 150)
        if not isinstance(dpi, (int, float)) or dpi <= 0:
            raise ValueError("dpi must be a positive number")
        return _read_pdf(path, warn_pages, dpi)
    if extension == ".docx":
        return _read_docx(path)
    if extension in {".md", ".txt"}:
        return Doc(
            path.name,
            extension.lstrip("."),
            [{"n": 1, "text": path.read_text(encoding="utf-8-sig")}],
            [],
            False,
        )
    if extension in {".csv", ".xlsx"}:
        return _read_table(path)
    if extension in {".png", ".jpg", ".jpeg", ".webp"}:
        return _read_image(path)
    raise ValueError(
        f"Unsupported file type '{extension or '(no extension)'}'. "
        "Supported formats: PDF, DOCX, MD, TXT, CSV, XLSX, PNG, JPG, JPEG, WEBP."
    )
