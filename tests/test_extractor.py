import json
from types import SimpleNamespace

import pymupdf as fitz
from PIL import Image

from chain import ask as ask_with_chain
from exporters import write_json
from extractor import _chunks, run
from readers import Doc, read


def make_test_config(path):
    path.write_text("chunk_pages: 6\n", encoding="utf-8")


def test_certificate_preset_returns_fields_for_pdf_and_image(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    make_test_config(config)
    pdf_path = tmp_path / "certificate.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Certificate of Completion from Example Institute")
        pdf.save(pdf_path)
    image_path = tmp_path / "certificate.png"
    Image.new("RGB", (10, 10), "white").save(image_path)

    slot = {
        "name": "certificate-test",
        "model": "certificate-test-model",
        "base_url_env": "CERTIFICATE_TEST_URL",
        "key_env": "CERTIFICATE_TEST_KEY",
        "vision": True,
        "local": False,
    }
    monkeypatch.setenv(slot["base_url_env"], "https://example.invalid/v1")
    monkeypatch.setenv(slot["key_env"], "test-key")
    image_requests = []

    def factory(**_kwargs):
        def create(**request):
            image_requests.append(isinstance(request["messages"][0]["content"], list))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "certificate_name": "Certificate of Completion",
                                    "recipient_name": "Ada Lovelace",
                                    "issuing_organization": "Example Institute",
                                }
                            )
                        )
                    )
                ]
            )

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    def mocked_ask(prompt, **kwargs):
        return ask_with_chain(
            prompt,
            slots=[slot],
            client_factory=factory,
            **kwargs,
        )

    for path in (pdf_path, image_path):
        result = run(
            read(path),
            "certificate",
            ask_fn=mocked_ask,
            config_path=config,
        )
        record = result["records"][0]
        assert result["status"] == "ok"
        assert record["certificate_name"] == "Certificate of Completion"
        assert record["recipient_name"] == "Ada Lovelace"
        assert record["issuing_organization"] == "Example Institute"
        assert record["expiry_date"] is None
        assert record["_model"] == "certificate-test-model"
    assert image_requests == [False, True]


def test_extractor_records_model_used_after_fallback(monkeypatch, tmp_path):
    import extractor

    config = tmp_path / "config.yaml"
    make_test_config(config)
    text_path = tmp_path / "certificate.txt"
    text_path.write_text("A certificate.", encoding="utf-8")
    slots = [
        {
            "name": "first",
            "model": "model-one",
            "base_url_env": "FIRST_URL",
            "key_env": "FIRST_KEY",
            "vision": False,
            "local": False,
        },
        {
            "name": "second",
            "model": "model-two",
            "base_url_env": "SECOND_URL",
            "key_env": "SECOND_KEY",
            "vision": False,
            "local": False,
        },
    ]
    for model_slot in slots:
        monkeypatch.setenv(model_slot["base_url_env"], "https://example.invalid/v1")
        monkeypatch.setenv(model_slot["key_env"], "test-key")
    called = []

    def factory(**_kwargs):
        def create(**request):
            called.append(request["model"])
            if request["model"] == "model-one":
                raise RuntimeError("unavailable")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"certificate_name": "A certificate"}'
                        )
                    )
                ]
            )

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    def ask_fn(prompt, **kwargs):
        return ask_with_chain(
            prompt,
            slots=slots,
            client_factory=factory,
            **{key: value for key, value in kwargs.items() if key != "config_path"},
        )

    monkeypatch.setattr(extractor, "ask", ask_fn)
    result = run(read(text_path), "certificate", config_path=config)

    assert called == ["model-one", "model-two"]
    assert result["records"][0]["_model"] == "model-two"


def test_json_writer_writes_results(tmp_path):
    destination = tmp_path / "nested" / "result.json"
    results = [{"file": "a.txt", "status": "ok"}]

    returned_path = write_json(results, destination)

    assert returned_path == destination
    assert json.loads(destination.read_text(encoding="utf-8")) == results


def test_chunks_split_by_page_count_and_character_count():
    document = Doc(
        "long.txt",
        "txt",
        [
            {"n": 1, "text": "a" * 7},
            {"n": 2, "text": "b" * 14},
            {"n": 3, "text": "c"},
        ],
        [],
        False,
    )

    chunks = _chunks(document, chunk_pages=2, chunk_chars=10)

    assert [len(pages) for pages, _ in chunks] == [1, 1, 2]
    assert [sum(len(page["text"]) for page in pages) for pages, _ in chunks] == [
        7,
        10,
        5,
    ]


def test_per_file_chunks_merge_first_non_null_values(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("chunk_pages: 1\nchunk_chars: 12000\n", encoding="utf-8")
    document = Doc(
        "paper.txt",
        "txt",
        [{"n": 1, "text": "first"}, {"n": 2, "text": "second"}],
        [],
        False,
    )
    responses = iter(
        [
            {"title": "Original title", "abstract": None, "_page": 1, "_model": "m1"},
            {"title": "Different title", "abstract": "Abstract text", "_page": 2, "_model": "m2"},
        ]
    )
    result = run(document, "research", ask_fn=lambda *_args, **_kwargs: next(responses), config_path=config)

    assert result["status"] == "ok"
    assert result["records"] == [
        {
            "title": "Original title",
            "authors": None,
            "year": None,
            "abstract": "Abstract text",
            "methods": None,
            "key_findings": None,
            "limitations": None,
            "keywords": None,
            "_file": "paper.txt",
            "_page": 1,
            "_model": "m1",
        }
    ]


def test_multi_row_records_concatenate_and_drop_duplicate_rows(tmp_path):
    config = tmp_path / "config.yaml"
    make_test_config(config)
    config.write_text("chunk_pages: 1\nchunk_chars: 12000\n", encoding="utf-8")
    document = Doc(
        "statement.txt",
        "txt",
        [{"n": 1, "text": "Revenue 100"}, {"n": 2, "text": "Revenue 100; costs 30"}],
        [],
        False,
    )
    duplicate = {
        "line_item": "Revenue",
        "amount": 100,
        "currency": "USD",
    }
    responses = iter(
        [
            {"rows": [duplicate], "_model": "model-a"},
            {"rows": [duplicate, {"line_item": "Costs", "amount": 30, "currency": "USD"}], "_model": "model-b"},
        ]
    )

    result = run(
        document,
        "financial",
        ask_fn=lambda *_args, **_kwargs: next(responses),
        config_path=config,
    )

    assert result["status"] == "ok"
    assert result["records"] == [
        {
            "line_item": "Revenue",
            "amount": 100,
            "currency": "USD",
            "_page": 1,
            "_file": "statement.txt",
            "_model": "model-a",
        },
        {
            "line_item": "Costs",
            "amount": 30,
            "currency": "USD",
            "_page": 2,
            "_file": "statement.txt",
            "_model": "model-b",
        },
    ]


def test_all_presets_extract_small_fixture_documents(tmp_path):
    config = tmp_path / "config.yaml"
    make_test_config(config)
    document_text = (
        "Example University certificate. Research methods and key findings. "
        "Candidate: Ada Example. Statement period: 2025, revenue 100 USD."
    )
    document = Doc("fixture.txt", "txt", [{"n": 1, "text": document_text}], [], False)
    responses = {
        "certificate": {"certificate_name": "Example University certificate"},
        "financial": {
            "rows": [
                {
                    "statement_type": "Income statement",
                    "period": "2025",
                    "line_item": "Revenue",
                    "amount": 100,
                    "currency": "USD",
                }
            ]
        },
        "research": {"title": "Research methods and key findings"},
        "cv_data": {"name": "Ada Example"},
        "custom": {"requested_value": "fixture"},
    }

    for preset_key, response in responses.items():
        result = run(
            document,
            preset_key,
            command="Extract the visible details",
            ask_fn=lambda *_args, response=response, **_kwargs: response,
            config_path=config,
        )
        assert result["status"] == "ok"
        assert result["records"]
        if preset_key == "financial":
            assert result["records"][0]["line_item"] == "Revenue"
            assert result["records"][0]["_page"] == 1
            assert "page" not in result["records"][0]
        else:
            assert next(iter(response)) in result["records"][0]


def test_long_document_completes_through_six_page_chunks(tmp_path):
    config = tmp_path / "config.yaml"
    make_test_config(config)
    document = Doc(
        "long.pdf",
        "pdf",
        [{"n": number, "text": f"Page {number}"} for number in range(1, 56)],
        [],
        False,
    )
    calls = []

    def respond(_prompt, **_kwargs):
        calls.append(True)
        return {"certificate_name": "Long document"}

    result = run(document, "certificate", ask_fn=respond, config_path=config)

    assert len(calls) == 10
    assert result["status"] == "ok"
    assert result["records"][0]["certificate_name"] == "Long document"


def test_local_only_flag_is_passed_to_chain_call(tmp_path):
    config = tmp_path / "config.yaml"
    make_test_config(config)
    document = Doc("local.txt", "txt", [{"n": 1, "text": "Private text"}], [], False)
    local_flags = []

    def respond(_prompt, **kwargs):
        local_flags.append(kwargs["local_only"])
        return {"certificate_name": "Private"}

    run(document, "certificate", local_only=True, ask_fn=respond, config_path=config)

    assert local_flags == [True]
