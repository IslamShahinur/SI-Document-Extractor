from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from chain import ask
from exporters import write_csv, write_docx, write_json, write_md, write_xlsx
from extractor import run
from readers import read


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
PRESETS_PATH = ROOT / "presets.json"
OUTPUT_PATH = ROOT / "output"
EXPORTERS = {
    "xlsx": write_xlsx,
    "csv": write_csv,
    "docx": write_docx,
    "md": write_md,
    "json": write_json,
}

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO)


class _RunEventHandler(logging.Handler):
    def __init__(self, events: list[str]) -> None:
        super().__init__(level=logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(self.format(record))


st.set_page_config(page_title="SI Document Extractor", layout="wide")
st.markdown(
    """
    <style>
    h1, h2, h3 { color: #D4AF37; }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid rgba(201, 162, 39, 0.3);
        border-radius: 8px;
        padding: 16px;
    }
    div.stButton > button[kind="primary"] {
        background: #D4AF37;
        color: #0B1426;
        border: 1px solid #B8860B;
        font-weight: 700;
    }
    div[data-testid="stProgressBar"] > div > div { background: #D4AF37; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _read_yaml() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _read_presets() -> dict[str, Any]:
    with PRESETS_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _default_command(preset: dict[str, Any]) -> str:
    keys = [field["key"] for field in preset.get("fields", [])]
    if not keys:
        return ""
    row_unit = (
        "line item" if preset.get("mode") == "multi_row" else "file"
    )
    return f"Extract {', '.join(keys)}; one row per {row_unit}."


def _preset_changed(presets: dict[str, Any]) -> None:
    st.session_state["command"] = _default_command(
        presets[st.session_state["preset"]]
    )


def _clear_uploads() -> None:
    st.session_state["uploaded_files"] = None


def _slot_for_model(model: str | None, slots: list[dict[str, Any]]) -> str:
    for index, slot in enumerate(slots, start=1):
        if slot.get("model") == model:
            return f"slot {index} ({slot.get('name', model)})"
    return "slot unknown"


def _clean_cell(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if not isinstance(value, (list, dict, tuple)) and pd.isna(value):
        return None
    return value


def _edited_results(
    original: list[dict[str, Any]], edited: pd.DataFrame
) -> list[dict[str, Any]]:
    rows_by_file: dict[str, list[dict[str, Any]]] = {}
    for row in edited.to_dict(orient="records"):
        record = {key: _clean_cell(value) for key, value in row.items()}
        filename = str(record.get("_file") or record.get("file") or "")
        rows_by_file.setdefault(filename, []).append(record)

    updated = []
    for result in original:
        result_copy = dict(result)
        filename = str(result.get("file", ""))
        result_copy["records"] = rows_by_file.pop(filename, [])
        updated.append(result_copy)
    for filename, records in rows_by_file.items():
        updated.append(
            {
                "file": filename,
                "preset": "",
                "status": "ok",
                "records": records,
                "error": None,
            }
        )
    return updated


def _write_run_log(
    results: list[dict[str, Any]], folder: Path, events: list[str] | None = None
) -> None:
    lines = [f"Run: {folder.name}"]
    for result in results:
        record = (result.get("records") or [{}])[0]
        lines.append(
            f"{result.get('file')}: {result.get('status')} "
            f"model={record.get('_model', 'none')} "
            f"error={result.get('error') or 'none'}"
        )
    if events:
        lines.extend(["Events:", *events])
    (folder / "run.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save_exports(
    results: list[dict[str, Any]], folder: Path, formats: list[str]
) -> list[Path]:
    paths = []
    for extension in formats:
        paths.append(EXPORTERS[extension](results, folder / f"results.{extension}"))
    return paths


def _main_view(presets: dict[str, Any], config: dict[str, Any]) -> None:
    st.subheader("1  FILES")
    uploaded_files = st.file_uploader(
        "Drop files here or browse",
        type=["pdf", "docx", "md", "txt", "csv", "xlsx", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="uploaded_files",
        label_visibility="collapsed",
    )
    if uploaded_files:
        st.caption("  ·  ".join(upload.name for upload in uploaded_files))
        st.button("Clear files", on_click=_clear_uploads)
    else:
        st.caption("Drop a document to begin")

    st.subheader("2  PRESET")
    preset_keys = list(presets)
    if "preset" not in st.session_state or st.session_state["preset"] not in preset_keys:
        st.session_state["preset"] = (
            "certificate" if "certificate" in presets else preset_keys[0]
        )
        st.session_state["command"] = _default_command(
            presets[st.session_state["preset"]]
        )
    col_preset, col_local = st.columns([2, 1])
    with col_preset:
        st.selectbox(
            "Preset",
            preset_keys,
            format_func=lambda key: presets[key].get("label", key),
            key="preset",
            on_change=_preset_changed,
            args=(presets,),
        )
    with col_local:
        local_only = st.checkbox("Local only (sensitive)", key="local_only")

    st.subheader("3  COMMAND")
    command = st.text_area(
        "Command",
        key="command",
        placeholder="Describe the information to extract",
        label_visibility="collapsed",
    )

    st.subheader("4  OUTPUT")
    format_cols = st.columns(len(EXPORTERS))
    selected_formats = []
    for col, extension in zip(format_cols, EXPORTERS):
        with col:
            if st.checkbox(
                extension.upper(),
                value=extension in {"xlsx", "csv", "md"},
                key=f"format_{extension}",
            ):
                selected_formats.append(extension)

    can_extract = bool(uploaded_files) and bool(selected_formats) and (
        st.session_state["preset"] != "custom" or bool(command.strip())
    )
    if st.button("▶ EXTRACT", type="primary", disabled=not can_extract):
        results: list[dict[str, Any]] = []
        run_events: list[str] = []
        event_handler = _RunEventHandler(run_events)
        event_handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s")
        )
        run_loggers = [
            logging.getLogger(name) for name in ("chain", "extractor", "readers")
        ]
        previous_levels = [logger.level for logger in run_loggers]
        for logger in run_loggers:
            logger.setLevel(logging.INFO)
            logger.addHandler(event_handler)
        progress = st.progress(0)
        status_area = st.container()
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                for index, upload in enumerate(uploaded_files):
                    temporary_path = (
                        Path(temporary_directory) / f"{index}_{Path(upload.name).name}"
                    )
                    temporary_path.write_bytes(upload.getvalue())
                    try:
                        document = read(temporary_path, config_path=CONFIG_PATH)
                        document.name = upload.name
                        warn_pages = int(config.get("warn_pages", 200))
                        if len(document.pages) > warn_pages:
                            with status_area:
                                st.warning(
                                    f"{upload.name} has {len(document.pages)} pages; "
                                    f"the configured warning threshold is {warn_pages}."
                                )
                        result = run(
                            document,
                            st.session_state["preset"],
                            command,
                            local_only,
                            presets_path=PRESETS_PATH,
                            config_path=CONFIG_PATH,
                        )
                        results.append(result)
                        record = (result.get("records") or [{}])[0]
                        slot_label = _slot_for_model(
                            record.get("_model"), config.get("slots", [])
                        )
                        with status_area:
                            message = f"{upload.name} — {result['status']} — {slot_label}"
                            if result["status"] == "failed":
                                st.error(
                                    f"{message}: {result.get('error') or 'Extraction failed'}"
                                )
                            elif result["status"] == "partial":
                                st.warning(
                                    f"{message}: {result.get('error') or 'Some chunks failed'}"
                                )
                            else:
                                st.success(message)
                    except Exception as error:
                        results.append(
                            {
                                "file": upload.name,
                                "preset": st.session_state["preset"],
                                "status": "failed",
                                "records": [],
                                "error": str(error),
                            }
                        )
                        with status_area:
                            st.error(f"{upload.name} — failed: {error}")
                    progress.progress((index + 1) / len(uploaded_files))
        finally:
            for logger, previous_level in zip(run_loggers, previous_levels):
                logger.removeHandler(event_handler)
                logger.setLevel(previous_level)

        run_folder = OUTPUT_PATH / datetime.now().strftime("%Y-%m-%d_%H%M")
        run_folder.mkdir(parents=True, exist_ok=True)
        _write_run_log(results, run_folder, run_events)
        _save_exports(results, run_folder, selected_formats)
        st.session_state["results"] = results
        st.session_state["run_folder"] = str(run_folder)
        st.session_state["run_formats"] = selected_formats
        st.session_state["run_events"] = run_events
        st.session_state.pop("preview_editor", None)
        st.success(f"Run saved to {run_folder.name}")

    results = st.session_state.get("results", [])
    if results:
        st.subheader("PREVIEW (editable)")
        records = [record for result in results for record in result.get("records", [])]
        frame = pd.DataFrame(records)
        if frame.empty:
            frame = pd.DataFrame(columns=["_file", "_page", "_model"])
        edited_frame = st.data_editor(
            frame,
            width="stretch",
            num_rows="dynamic",
            key="preview_editor",
        )
        edited_results = _edited_results(results, edited_frame)
        run_folder = Path(st.session_state["run_folder"])
        paths = _save_exports(
            edited_results, run_folder, st.session_state["run_formats"]
        )
        _write_run_log(
            edited_results, run_folder, st.session_state.get("run_events", [])
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for path in paths:
                zip_file.write(path, path.name)
        st.download_button(
            "Download all",
            data=archive.getvalue(),
            file_name=f"{run_folder.name}_exports.zip",
            mime="application/zip",
        )


def _settings_view(config: dict[str, Any]) -> None:
    st.subheader("AI Chain (priority top → bottom)")
    slots = config.get("slots", [])
    slot_frame = pd.DataFrame(
        [
            {
                "name": slot.get("name", ""),
                "model": slot.get("model", ""),
                "vision": slot.get("vision", False),
                "local": slot.get("local", False),
                "base_url_env": slot.get("base_url_env", ""),
                "key_env": slot.get("key_env", ""),
            }
            for slot in slots
        ]
    )
    with st.form("settings_form"):
        edited_slots = st.data_editor(
            slot_frame,
            width="stretch",
            num_rows="fixed",
            column_config={
                "vision": st.column_config.CheckboxColumn(),
                "local": st.column_config.CheckboxColumn(),
            },
        )
        timeout = st.number_input(
            "Timeout (seconds)",
            min_value=1,
            max_value=3600,
            value=int(config.get("timeout", 60)),
        )
        retries = st.number_input(
            "Retries per slot",
            min_value=0,
            max_value=10,
            value=int(config.get("retries", 1)),
        )
        save = st.form_submit_button("Save to config.yaml")
    for index, slot in enumerate(slots, start=1):
        base_set = bool(os.getenv(slot.get("base_url_env", "")))
        key_set = bool(os.getenv(slot.get("key_env", "")))
        st.caption(
            f"#{index} {slot.get('name')}: URL {'set' if base_set else 'missing'} · "
            f"key {'set' if key_set else 'missing'}"
        )
    if save:
        updated = dict(config)
        updated["slots"] = edited_slots.to_dict(orient="records")
        updated["timeout"] = int(timeout)
        updated["retries"] = int(retries)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            yaml.safe_dump(updated, file, sort_keys=False, allow_unicode=True)
        st.success("Saved config.yaml")

    if st.button("Test chain"):
        prompt = 'Return this JSON object: {"status": "ok"}'
        for index, slot in enumerate(slots, start=1):
            started = time.monotonic()
            try:
                ask(
                    prompt,
                    slots=[slot],
                    timeout=float(config.get("timeout", 60)),
                    retries=int(config.get("retries", 1)),
                    config_path=CONFIG_PATH,
                )
                elapsed = time.monotonic() - started
                st.success(f"#{index} {slot.get('name')} · ok · {elapsed:.2f}s")
            except Exception as error:
                elapsed = time.monotonic() - started
                st.error(f"#{index} {slot.get('name')} · fail · {elapsed:.2f}s · {error}")


def _log_view() -> None:
    st.subheader("Runs")
    runs = sorted(
        (path for path in OUTPUT_PATH.iterdir() if path.is_dir() and (path / "run.log").is_file()),
        key=lambda path: path.name,
        reverse=True,
    ) if OUTPUT_PATH.is_dir() else []
    if not runs:
        st.caption("No runs yet.")
        return
    for path in runs[:10]:
        run_lines = (path / "run.log").read_text(encoding="utf-8").splitlines()[1:]
        run_lines = run_lines[:run_lines.index("Events:")] if "Events:" in run_lines else run_lines
        statuses = [line.split(": ", 1)[1].split(" ", 1)[0] for line in run_lines if ": " in line]
        st.caption(
            f"{path.name} · {len(statuses)} files · "
            f"ok {statuses.count('ok')} · partial {statuses.count('partial')} · "
            f"failed {statuses.count('failed')}"
        )
    selected = st.selectbox("Run", runs, format_func=lambda path: path.name)
    with st.container(border=True):
        st.code((selected / "run.log").read_text(encoding="utf-8"), language="text")


def main() -> None:
    presets = _read_presets()
    config = _read_yaml()
    title, settings_button, log_button = st.columns([6, 1, 1])
    with title:
        st.title("◆ SI Document Extractor")
    with settings_button:
        if st.button("Settings"):
            st.session_state["view"] = "settings"
    with log_button:
        if st.button("Log"):
            st.session_state["view"] = "log"

    view = st.session_state.get("view", "main")
    if view != "main" and st.button("← Main"):
        st.session_state["view"] = "main"
        view = "main"
    if view == "settings":
        _settings_view(config)
    elif view == "log":
        _log_view()
    else:
        _main_view(presets, config)


main()
