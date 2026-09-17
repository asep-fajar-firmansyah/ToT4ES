#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Chat clients for the ToT4ES search engine (DICE LLM API and local Ollama)."""

from __future__ import annotations

import os
import logging
import json
import re
from typing import Any, Dict, List, Optional

import requests

LOGGER = logging.getLogger(__name__)

PROVIDER_DICE = "dice"
PROVIDER_OLLAMA = "ollama"
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", PROVIDER_DICE).strip().lower()

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:0.8b"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_ARRAY = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


class LLMAPIError(RuntimeError):
    """Raised when the configured chat completion API cannot be used."""


def _clean_answer(text: str) -> str:
    """Drop reasoning preambles and code fences local models wrap answers in."""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    fenced = _CODE_FENCE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("["):
        array = _JSON_ARRAY.search(cleaned)
        if array:
            cleaned = array.group(0)
    return cleaned.strip()


class OpenAICompatibleChat:
    """Adapt an OpenAI-compatible chat completion endpoint to ToT4ES."""

    provider_id = PROVIDER_DICE

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DICE_LLM_API_KEY")
        self.endpoint = (endpoint or os.getenv(
            "DICE_LLM_ENDPOINT",
            "https://dice-llm-api.cs.uni-paderborn.de/v1/chat/completions",
        )).rstrip("/")
        self.model_id = model or os.getenv("DICE_LLM_MODEL", "general-purpose")
        # The gateway can spend a long time on reasoning_content before the
        # final answer, so allow a generous default and let it be tuned.
        self.timeout = timeout or int(os.getenv("DICE_LLM_TIMEOUT", "300"))
        self.verbose = os.getenv("DICE_LLM_VERBOSE", "false").lower() in {"1", "true", "yes", "on"}
        self.log_max_chars = max(500, int(os.getenv("DICE_LLM_LOG_MAX_CHARS", "12000")))
        if not self.api_key:
            raise LLMAPIError("DICE_LLM_API_KEY is not configured on the server.")

    def _log_text(self, value: object) -> str:
        text = str(value)
        if len(text) <= self.log_max_chars:
            return text
        return f"{text[:self.log_max_chars]}... [truncated]"

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_new_tokens: int = 1024,
        n: int = 1,
        do_sample: Optional[bool] = None,
    ) -> List[str]:
        # The gateway rejects extra fields with 400, so send only model+messages.
        payload: Dict[str, Any] = {"model": self.model_id, "messages": messages}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.verbose:
            LOGGER.info(
                "LLM REQUEST endpoint=%s timeout=%ss body=%s",
                self.endpoint,
                self.timeout,
                self._log_text(json.dumps(payload, ensure_ascii=False, indent=2)),
            )
            LOGGER.info(
                "LLM REQUEST CURL: curl --location %r --header 'Authorization: Bearer <redacted>' --header 'Content-Type: application/json' --data %r",
                self.endpoint,
                json.dumps(payload, ensure_ascii=False),
            )
        try:
            response = requests.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            if self.verbose:
                LOGGER.info(
                    "LLM RESPONSE status=%s content_type=%s body=%s",
                    response.status_code,
                    response.headers.get("Content-Type", ""),
                    self._log_text(response.text),
                )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout as exc:
            if self.verbose:
                LOGGER.exception("LLM REQUEST TIMED OUT after %ss: %s", self.timeout, exc)
            raise LLMAPIError(
                f"LLM API did not respond within {self.timeout}s. "
                "Increase DICE_LLM_TIMEOUT if the model is slow to finish reasoning."
            ) from exc
        except requests.RequestException as exc:
            if self.verbose:
                LOGGER.exception("LLM REQUEST FAILED: %s", exc)
            raise LLMAPIError(f"LLM API request failed: {exc}") from exc
        except ValueError as exc:
            if self.verbose:
                LOGGER.exception("LLM RESPONSE JSON PARSE FAILED: %s", exc)
            raise LLMAPIError("LLM API returned invalid JSON.") from exc

        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise LLMAPIError(f"LLM API error: {message}")

        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, dict):
            choices = [choices]
        if not isinstance(choices, list):
            keys = ", ".join(sorted(data.keys())) if isinstance(data, dict) else type(data).__name__
            raise LLMAPIError(f"LLM API response did not contain choices (keys: {keys}).")

        outputs: List[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message", {})
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                outputs.append(_clean_answer(content))
                continue
            if isinstance(content, list):
                parts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                combined = _clean_answer("".join(parts))
                if combined:
                    outputs.append(combined)
                    continue
            if isinstance(message, dict):
                for field in ("reasoning_content", "reasoning", "text"):
                    reasoning = message.get(field)
                    if isinstance(reasoning, str) and reasoning.strip():
                        outputs.append(_clean_answer(reasoning))
                        break
                else:
                    continue
                continue
            legacy_text = choice.get("text")
            if isinstance(legacy_text, str) and legacy_text.strip():
                outputs.append(_clean_answer(legacy_text))
        if not outputs:
            raise LLMAPIError("LLM API returned choices without usable text content.")
        return outputs

    def __repr__(self) -> str:
        return f"OpenAICompatibleChat(model={self.model_id!r}, endpoint={self.endpoint!r})"


class OllamaChat:
    """Adapt a local Ollama server (`/api/chat`) to the ToT4ES chat interface."""

    provider_id = PROVIDER_OLLAMA

    def __init__(
        self,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        base = (endpoint or os.getenv("OLLAMA_ENDPOINT", DEFAULT_OLLAMA_ENDPOINT)).rstrip("/")
        for suffix in ("/api/chat", "/v1/chat/completions", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        self.base_url = base.rstrip("/")
        self.endpoint = f"{self.base_url}/api/chat"
        self.model_id = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.timeout = timeout or int(os.getenv("OLLAMA_TIMEOUT", "300"))
        # Reasoning preambles eat the small token budgets ToT4ES uses, so
        # thinking is off unless the user asks for it.
        self.think = os.getenv("OLLAMA_THINK", "false").lower() in {"1", "true", "yes", "on"}
        self._think_supported = True
        self.min_predict = int(os.getenv("OLLAMA_MIN_PREDICT", "256"))
        self.verbose = os.getenv(
            "OLLAMA_VERBOSE", os.getenv("DICE_LLM_VERBOSE", "false")
        ).lower() in {"1", "true", "yes", "on"}
        self.log_max_chars = max(500, int(os.getenv("DICE_LLM_LOG_MAX_CHARS", "12000")))

    def _log_text(self, value: object) -> str:
        text = str(value)
        if len(text) <= self.log_max_chars:
            return text
        return f"{text[:self.log_max_chars]}... [truncated]"

    def list_models(self) -> List[str]:
        """Return the model tags served by the configured Ollama instance."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LLMAPIError(f"Ollama is not reachable at {self.base_url}: {exc}") from exc
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        return [entry["name"] for entry in models if isinstance(entry, dict) and entry.get("name")]

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_new_tokens: int = 1024,
        n: int = 1,
        do_sample: Optional[bool] = None,
    ) -> List[str]:
        # Ollama has no `n` parameter, so sample sequentially.
        return [
            self._complete(messages, temperature, max_new_tokens)
            for _ in range(max(1, int(n)))
        ]

    def _complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_new_tokens: int,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": max(int(max_new_tokens), self.min_predict),
            },
        }
        if self._think_supported:
            payload["think"] = self.think

        data = self._post(payload)

        if isinstance(data, dict) and data.get("error"):
            raise LLMAPIError(f"Ollama error: {data['error']}")

        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        text = _clean_answer(content) if isinstance(content, str) else ""
        if not text and isinstance(message, dict):
            thinking = message.get("thinking")
            if isinstance(thinking, str):
                text = _clean_answer(thinking)
        if not text:
            raise LLMAPIError("Ollama returned an empty message.")
        return text

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.verbose:
            LOGGER.info(
                "OLLAMA REQUEST endpoint=%s timeout=%ss body=%s",
                self.endpoint,
                self.timeout,
                self._log_text(json.dumps(payload, ensure_ascii=False, indent=2)),
            )
        try:
            response = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            if self.verbose:
                LOGGER.info(
                    "OLLAMA RESPONSE status=%s body=%s",
                    response.status_code,
                    self._log_text(response.text),
                )
            if (
                response.status_code == 400
                and "think" in payload
                and "think" in response.text.lower()
            ):
                self._think_supported = False
                payload.pop("think")
                return self._post(payload)
            response.raise_for_status()
            return response.json()
        except requests.Timeout as exc:
            raise LLMAPIError(
                f"Ollama did not respond within {self.timeout}s. "
                "Increase OLLAMA_TIMEOUT or use a smaller model."
            ) from exc
        except requests.RequestException as exc:
            raise LLMAPIError(
                f"Ollama request to {self.endpoint} failed: {exc}. "
                "Is `ollama serve` running and the model pulled?"
            ) from exc
        except ValueError as exc:
            raise LLMAPIError("Ollama returned invalid JSON.") from exc

    def __repr__(self) -> str:
        return f"OllamaChat(model={self.model_id!r}, endpoint={self.endpoint!r})"


def create_chat_client(provider: Optional[str] = None, **kwargs):
    """Build the chat client for `provider` ('dice' or 'ollama')."""
    name = (provider or DEFAULT_PROVIDER or PROVIDER_DICE).strip().lower()
    if name == PROVIDER_OLLAMA:
        return OllamaChat(**kwargs)
    if name in {PROVIDER_DICE, "dice-llm", "dice_llm", "openai"}:
        return OpenAICompatibleChat(**kwargs)
    raise LLMAPIError(f"Unknown LLM provider {name!r}. Use 'dice' or 'ollama'.")


def describe_providers() -> List[Dict[str, Any]]:
    """Report the selectable providers and whether they are usable right now."""
    dice_ready = bool(os.getenv("DICE_LLM_API_KEY"))
    ollama = OllamaChat()
    try:
        ollama_models = ollama.list_models()
        ollama_ready = True
        ollama_detail = None
    except LLMAPIError as exc:
        ollama_models = []
        ollama_ready = False
        ollama_detail = str(exc)

    return [
        {
            "id": PROVIDER_DICE,
            "label": "DICE LLM API",
            "model": os.getenv("DICE_LLM_MODEL", "general-purpose"),
            "models": [],
            "available": dice_ready,
            "detail": None if dice_ready else "DICE_LLM_API_KEY is not configured on the server.",
            "default": DEFAULT_PROVIDER != PROVIDER_OLLAMA,
        },
        {
            "id": PROVIDER_OLLAMA,
            "label": "Ollama (local)",
            "model": ollama.model_id,
            "models": ollama_models,
            "available": ollama_ready,
            "detail": ollama_detail,
            "default": DEFAULT_PROVIDER == PROVIDER_OLLAMA,
        },
    ]
