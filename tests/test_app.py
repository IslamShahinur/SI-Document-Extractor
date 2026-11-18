import app


def test_run_log_keeps_error_for_missing_or_empty_records(tmp_path):
    results = [
        {
            "file": "missing-records.pdf",
            "status": "failed",
            "error": "Model slot 'Mistral' requires SECONDARY_API_KEY",
        },
        {
            "file": "empty-records.pdf",
            "status": "failed",
            "records": [],
            "error": "Model reply was not valid JSON",
        },
    ]

    app._write_run_log(results, tmp_path)

    run_log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "missing-records.pdf: failed model=none" in run_log
    assert "Model slot 'Mistral' requires SECONDARY_API_KEY" in run_log
    assert "empty-records.pdf: failed model=none" in run_log
    assert "Model reply was not valid JSON" in run_log
