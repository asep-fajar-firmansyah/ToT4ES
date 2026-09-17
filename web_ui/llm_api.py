#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""OpenAI-compatible chat client for the ToT4ES search engine."""

from __future__ import annotations

import os
import logging
import json
from typing import Dict, List, Optional

import requests

LOGGER = logging.getLogger(__name__)


class LLMAPIError(RuntimeError):
    """Raised when the configured chat completion API cannot be used."""


class OpenAICompatibleChat:
    """Adapt an OpenAI-compatible chat completion endpoint to ToT4ES."""

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
        # Keep the default body identical to the verified gateway example.
        payload = {"model": self.model_id, "messages": messages}
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
                outputs.append(content.strip())
                continue
            if isinstance(content, list):
                parts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                combined = "".join(parts).strip()
                if combined:
                    outputs.append(combined)
                    continue
            if isinstance(message, dict):
                for field in ("reasoning_content", "reasoning", "text"):
                    reasoning = message.get(field)
                    if isinstance(reasoning, str) and reasoning.strip():
                        outputs.append(reasoning.strip())
                        break
                else:
                    continue
                continue
            legacy_text = choice.get("text")
            if isinstance(legacy_text, str) and legacy_text.strip():
                outputs.append(legacy_text.strip())
        if not outputs:
            raise LLMAPIError("LLM API returned choices without usable text content.")
        return outputs

    def __repr__(self) -> str:
        return f"OpenAICompatibleChat(model={self.model_id!r}, endpoint={self.endpoint!r})"
