from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
import yaml

from readers import ImagePage


LOGGER = logging.getLogger(__name__)
MISTRAL_MAX_TOKENS = 4096


def load_config(config_path: str | Path = "config.yaml") -> dict[str, Any]:
    load_dotenv()
    with Path(config_path).open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict) or not isinstance(config.get("slots"), list):
        raise ValueError("config.yaml must define a slots list")
    for slot in config["slots"]:
        _validate_slot_env_names(slot)
    return config


def _validate_slot_env_names(slot: dict[str, Any]) -> tuple[str, str]:
    name = slot.get("name", slot.get("model", "unnamed"))
    env_names = []
    for field in ("base_url_env", "key_env"):
        env_name = slot.get(field)
        if not isinstance(env_name, str) or not env_name.strip():
            raise ValueError(
                f"Model slot '{name}' must set {field} to an environment variable name"
            )
        env_name = env_name.strip()
        if env_name.lower().startswith(("http", "sk-")):
            raise ValueError(
                f"Model slot '{name}' {field} must be an environment variable name, "
                "not a URL or API key"
            )
        env_names.append(env_name)
    return env_names[0], env_names[1]


def _slot_credentials(slot: dict[str, Any]) -> tuple[str, str]:
    base_url_env, key_env = _validate_slot_env_names(slot)
    missing = [
        env_name
        for env_name in (base_url_env, key_env)
        if not os.getenv(env_name)
    ]
    if missing:
        name = slot.get("name", slot.get("model", "unnamed"))
        raise ValueError(
            f"Model slot '{name}' requires non-empty environment variable(s): "
            f"{', '.join(missing)}"
        )
    return os.environ[key_env], os.environ[base_url_env]


def _content_to_messages(
    prompt: str,
    images: list[ImagePage],
    *,
    model: str | None = None,
) -> list[dict[str, Any]]:
    if not images:
        return [{"role": "user", "content": prompt}]

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    mistral_vision = bool(model and model.lower().startswith("pixtral-"))
    for image in images:
        encoded = base64.b64encode(image["png_bytes"]).decode("ascii")
        image_url = f"data:image/png;base64,{encoded}"
        content.append(
            {
                "type": "image_url",
                "image_url": image_url if mistral_vision else {"url": image_url},
            }
        )
    return [{"role": "user", "content": content}]


def _build_payload(
    model: str, prompt: str, images: list[ImagePage]
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _content_to_messages(prompt, images, model=model),
        "response_format": {"type": "json_object"},
    }
    if model.lower().startswith("pixtral-"):
        payload["max_tokens"] = MISTRAL_MAX_TOKENS
    return payload


def _redact_log_value(
    value: Any, *, secrets: list[str], prompt_texts: list[str]
) -> str:
    sensitive_fields = {
        "apikey",
        "authorization",
        "headers",
        "key",
        "password",
        "prompt",
        "messages",
        "secret",
        "token",
    }

    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            redacted = {}
            for key, nested in item.items():
                normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
                redacted[key] = (
                    "[redacted]"
                    if normalized_key in sensitive_fields
                    or normalized_key.endswith(("key", "token"))
                    else redact(nested)
                )
            return redacted
        if isinstance(item, list):
            return [redact(nested) for nested in item]
        if isinstance(item, str):
            for sensitive in [*secrets, *prompt_texts]:
                if sensitive:
                    item = item.replace(sensitive, "[redacted]")
            item = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", item)
            item = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[redacted]", item)
        return item

    value = redact(value)
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text.replace("\r", " ").replace("\n", " ")


def _message_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(
                item["text"]
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            )
    return texts


def _parse_json(reply: str) -> dict[str, Any]:
    if not isinstance(reply, str):
        raise ValueError("Model reply must be text")
    cleaned = reply.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        result = None
        for index, character in enumerate(cleaned):
            if character != "{":
                continue
            try:
                result, _ = decoder.raw_decode(cleaned[index:])
                break
            except json.JSONDecodeError:
                continue
        if result is None:
            raise original_error
    if not isinstance(result, dict):
        raise ValueError("Model reply must be a JSON object")
    return result


def ask(
    prompt: str,
    images: list[ImagePage] | None = None,
    local_only: bool = False,
    *,
    slots: list[dict[str, Any]] | None = None,
    config_path: str | Path = "config.yaml",
    client_factory: Any = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> dict[str, Any]:
    image_list = images or []
    config = load_config(config_path) if slots is None else {}
    configured_slots = slots if slots is not None else config["slots"]
    timeout_seconds = timeout if timeout is not None else config.get("timeout", 60)
    retry_count = retries if retries is not None else config.get("retries", 0)
    if not isinstance(retry_count, int) or retry_count < 0:
        raise ValueError("retries must be a non-negative integer")
    factory = client_factory or OpenAI
    for slot in configured_slots:
        _validate_slot_env_names(slot)
    eligible = [
        slot
        for slot in configured_slots
        if (not local_only or slot.get("local", False))
        and (not image_list or slot.get("vision", False))
    ]
    if not eligible:
        raise RuntimeError("No configured model slots are eligible for this request")

    failures: list[str] = []
    failed_attempts = 0
    for slot_index, slot in enumerate(eligible):
        if slot_index and failed_attempts:
            LOGGER.warning(
                "model_fallback next_slot=%s model=%s after_failures=%s",
                slot.get("name", slot.get("model", "unnamed")),
                slot.get("model", "unnamed"),
                failed_attempts,
            )
        name = slot.get("name", slot.get("model", "unnamed"))
        model = slot["model"]
        try:
            api_key, base_url = _slot_credentials(slot)
        except ValueError as error:
            failure = str(error)
            failures.append(failure)
            LOGGER.warning(
                "model_slot_skipped slot=%s model=%s reason=%s",
                name,
                model,
                failure,
            )
            continue
        payload = _build_payload(model, prompt, image_list)
        for _attempt in range(retry_count + 1):
            started = time.monotonic()
            outcome = "error"
            try:
                client = factory(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                )

                def request(request_messages: list[dict[str, Any]]) -> str:
                    request_payload = dict(payload)
                    request_payload["messages"] = request_messages
                    response = client.chat.completions.create(
                        **request_payload
                    )
                    content = response.choices[0].message.content
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("Model returned an empty or non-text reply")
                    return content

                reply = request(payload["messages"])
                try:
                    parsed = _parse_json(reply)
                except (json.JSONDecodeError, ValueError):
                    repair_messages = payload["messages"] + [
                        {"role": "assistant", "content": reply},
                        {
                            "role": "user",
                            "content": "Return the same information as valid JSON only. Do not add information.",
                        },
                    ]
                    try:
                        parsed = _parse_json(request(repair_messages))
                    except (json.JSONDecodeError, ValueError) as repair_error:
                        raise ValueError(
                            "Model reply was not a JSON object after one repair "
                            f"attempt: {repair_error}"
                        ) from repair_error
                parsed["_model"] = model
                outcome = "ok"
                return parsed
            except Exception as error:
                failure_type = type(error).__name__
                prompt_texts = _message_texts(payload["messages"])
                safe_error = _redact_log_value(
                    str(error), secrets=[api_key], prompt_texts=prompt_texts
                )
                status_code = getattr(error, "status_code", None)
                response_body = getattr(error, "body", None)
                safe_body = _redact_log_value(
                    response_body, secrets=[api_key], prompt_texts=prompt_texts
                )
                failure = f"{failure_type}: {safe_error}"
                if status_code is not None:
                    failure += (
                        f" (HTTP {status_code}; response body: {safe_body})"
                    )
                failures.append(failure)
                failed_attempts += 1
                outcome = f"failed:{failure_type}"
                LOGGER.warning(
                    "model_attempt_failed slot=%s model=%s error_type=%s "
                    "error=%s http_status=%s response_body=%s",
                    name,
                    model,
                    failure_type,
                    safe_error,
                    status_code,
                    safe_body if status_code is not None else "none",
                )
            finally:
                LOGGER.info(
                    "model_attempt slot=%s model=%s seconds=%.2f outcome=%s",
                    name,
                    model,
                    time.monotonic() - started,
                    outcome,
                )

    raise RuntimeError(f"All eligible model slots failed: {'; '.join(failures)}")
