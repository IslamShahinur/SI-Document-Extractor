from types import SimpleNamespace

import pytest

from chain import _build_payload, ask, load_config


def slot(name, model, *, vision=False, local=False):
    return {
        "name": name,
        "model": model,
        "base_url_env": f"{name.upper()}_URL",
        "key_env": f"{name.upper()}_KEY",
        "vision": vision,
        "local": local,
    }


def install_credentials(monkeypatch, slots):
    for model_slot in slots:
        monkeypatch.setenv(model_slot["base_url_env"], "https://example.invalid/v1")
        monkeypatch.setenv(model_slot["key_env"], "test-key")


def fake_factory(handler, calls):
    def factory(**_kwargs):
        def create(**request):
            calls.append(request["model"])
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=handler(request["model"], request)))
                ]
            )

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    return factory


def test_falls_back_in_slot_order_and_reports_answering_model(monkeypatch, caplog):
    slots = [slot("first", "model-one"), slot("second", "model-two")]
    install_credentials(monkeypatch, slots)
    calls = []

    def respond(model, _request):
        if model == "model-one":
            raise RuntimeError("unavailable")
        return '{"answer": "ok"}'

    result = ask("Extract", slots=slots, client_factory=fake_factory(respond, calls))

    assert calls == ["model-one", "model-two"]
    assert result == {"answer": "ok", "_model": "model-two"}
    assert "model_attempt_failed" in caplog.text
    assert "model_fallback" in caplog.text


def test_repairs_invalid_json_once_before_returning(monkeypatch):
    slots = [slot("first", "model-one")]
    install_credentials(monkeypatch, slots)
    calls = []
    responses = iter(["not json", '```json\n{"answer": "fixed"}\n```'])
    factory = fake_factory(lambda _model, _request: next(responses), calls)

    result = ask("Extract", slots=slots, client_factory=factory)

    assert len(calls) == 2
    assert result == {"answer": "fixed", "_model": "model-one"}


def test_retries_a_slot_before_falling_back(monkeypatch):
    slots = [slot("first", "model-one"), slot("second", "model-two")]
    install_credentials(monkeypatch, slots)
    calls = []

    def respond(model, _request):
        calls.append(model)
        if model == "model-one" and calls.count(model) == 1:
            raise RuntimeError("temporary failure")
        return '{"answer": "ok"}'

    result = ask(
        "Extract",
        slots=slots,
        retries=1,
        client_factory=fake_factory(respond, []),
    )

    assert calls == ["model-one", "model-one"]
    assert result == {"answer": "ok", "_model": "model-one"}


def test_skips_non_vision_slots_for_image_requests(monkeypatch):
    slots = [slot("text", "text-model"), slot("vision", "vision-model", vision=True)]
    install_credentials(monkeypatch, slots)
    calls = []
    factory = fake_factory(lambda _model, _request: '{"answer": "ok"}', calls)

    result = ask(
        "Read image",
        images=[{"n": 1, "png_bytes": b"png"}],
        slots=slots,
        client_factory=factory,
    )

    assert calls == ["vision-model"]
    assert result["_model"] == "vision-model"


def test_local_only_uses_only_local_slots(monkeypatch):
    slots = [slot("remote", "remote-model"), slot("local", "local-model", local=True)]
    install_credentials(monkeypatch, slots)
    calls = []
    factory = fake_factory(lambda _model, _request: '{"answer": "local"}', calls)

    result = ask("Extract", local_only=True, slots=slots, client_factory=factory)

    assert calls == ["local-model"]
    assert result["answer"] == "local"


def test_no_eligible_slot_is_reported():
    with pytest.raises(RuntimeError, match="No configured model slots"):
        ask("Extract", local_only=True, slots=[slot("remote", "remote-model")])


def test_parses_json_object_surrounded_by_explanatory_text(monkeypatch):
    slots = [slot("alibaba", "qwen-vl-max")]
    install_credentials(monkeypatch, slots)
    calls = []
    factory = fake_factory(
        lambda _model, _request: 'Here is the result:\n{"answer": "ok"}\nDone.',
        calls,
    )

    result = ask("Extract", slots=slots, client_factory=factory)

    assert calls == ["qwen-vl-max"]
    assert result == {"answer": "ok", "_model": "qwen-vl-max"}


def test_builds_provider_compatible_vision_payloads():
    images = [{"n": 1, "png_bytes": b"png"}]

    mistral = _build_payload("pixtral-large-latest", "Read", images)
    mistral_image = mistral["messages"][0]["content"][1]["image_url"]
    assert mistral["response_format"] == {"type": "json_object"}
    assert mistral["max_tokens"] == 4096
    assert mistral_image == "data:image/png;base64,cG5n"

    alibaba = _build_payload("qwen-vl-max", "Read", images)
    alibaba_image = alibaba["messages"][0]["content"][1]["image_url"]
    assert alibaba["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in alibaba
    assert alibaba_image == {"url": "data:image/png;base64,cG5n"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url_env", "https://api.example.invalid/v1"),
        ("key_env", "sk-real-secret"),
    ],
)
def test_config_rejects_literal_urls_or_keys(tmp_path, field, value):
    import yaml

    model_slot = slot("bad-slot", "model")
    model_slot[field] = value
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"slots": [model_slot]}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="bad-slot.*environment variable name"):
        load_config(config_path)


def test_missing_credentials_skip_slot_once_without_retries(monkeypatch, caplog):
    model_slot = slot("unconfigured", "model")
    monkeypatch.delenv(model_slot["base_url_env"], raising=False)
    monkeypatch.delenv(model_slot["key_env"], raising=False)

    with pytest.raises(
        RuntimeError,
        match="unconfigured.*UNCONFIGURED_URL.*UNCONFIGURED_KEY",
    ):
        ask(
            "Extract",
            slots=[model_slot],
            retries=5,
            client_factory=lambda **_kwargs: pytest.fail("client must not be created"),
        )

    assert caplog.text.count("model_slot_skipped") == 1
    assert "UNCONFIGURED_URL" in caplog.text
    assert "UNCONFIGURED_KEY" in caplog.text


def test_logs_api_status_and_redacted_response_body(monkeypatch, caplog):
    slots = [slot("mistral", "pixtral-large-latest")]
    install_credentials(monkeypatch, slots)

    class BadRequestError(Exception):
        status_code = 400
        body = {
            "error": {
                "message": "invalid image payload",
                "authorization": "Bearer test-key",
                "prompt": "Extract confidential fields",
            }
        }

    def factory(**_kwargs):
        def create(**_request):
            raise BadRequestError("Bad request")

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    with pytest.raises(RuntimeError, match="HTTP 400"):
        ask(
            "Extract confidential fields",
            slots=slots,
            retries=0,
            client_factory=factory,
        )

    assert "http_status=400" in caplog.text
    assert "invalid image payload" in caplog.text
    assert "test-key" not in caplog.text
    assert "Extract confidential fields" not in caplog.text
