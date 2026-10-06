"""The single place that talks to Gemini.

Every call goes through `LLMClient.generate`, which handles:
  1. disk cache keyed by (task, prompt version, model, prompt text, schema) -> re-running
     evals costs nothing
  2. a concurrency limit (semaphore) + optional requests-per-minute cap per model
  3. retries with exponential backoff + jitter on transient errors (429/5xx/timeout)
  4. Pydantic validation of the JSON; ONE schema retry with the error message appended
  5. a fallback CHAIN of models if the primary keeps failing; a daily-quota 429 skips
     straight to the next model (retrying for hours is pointless)
  6. a record of every call (tokens, latency, cost, status) -> llm_calls table + Prometheus
"""
import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Awaitable, Callable, Literal, TypeVar

import yaml
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app import metrics
from app.schemas import LLMMeta
from app.settings import Settings, get_settings

T = TypeVar("T", bound=BaseModel)
Tier = Literal["fast", "strong"]
Recorder = Callable[[LLMMeta, str | None, str | None], Awaitable[None]]

TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: str

    def render(self, variables: dict) -> tuple[str, str]:
        return Template(self.system).substitute(variables), Template(self.user).substitute(variables)


@lru_cache
def load_pricing() -> dict[str, dict[str, float]]:
    path = get_settings().config_dir / "models.yaml"
    return yaml.safe_load(path.read_text())["pricing"]


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = load_pricing().get(model) or load_pricing()["default"]
    return round((input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000, 6)


class LLMClient:
    def __init__(self, settings: Settings | None = None, recorder: Recorder | None = None):
        self.settings = settings or get_settings()
        self.recorder = recorder
        self._client = genai.Client(api_key=self.settings.gemini_api_key) if self.settings.gemini_api_key else None
        self._sem: asyncio.Semaphore | None = None
        self._recent: dict[str, list[float]] = {}


    async def generate(self, *, task: str, prompt: Prompt, variables: dict, schema: type[T],
                       tier: Tier, call_id: str | None = None, feedback: str | None = None,
                       model_override: str | None = None) -> tuple[T, LLMMeta]:
        system, user = prompt.render(variables)
        if feedback:
            user = f"{user}\n\n{feedback}"
        primary = model_override or (self.settings.gemini_model_fast if tier == "fast" else self.settings.gemini_model_strong)
        chain = self.settings.fast_fallback_models if tier == "fast" else self.settings.fallback_models
        models = [primary] + [m for m in chain if m != primary]
        last_error = "no models configured"
        for i, model in enumerate(models):
            try:
                obj, meta = await self._generate_with_model(task, prompt.version, model, system, user, schema, call_id)
                if i > 0 and meta.status != "cache_hit":
                    meta.status = "fallback"
                return obj, meta
            except LLMError as e:
                last_error = str(e)
        raise LLMError(f"{task}: all models failed: {last_error}")


    async def _generate_with_model(self, task, prompt_version, model, system, user, schema, call_id):
        key = self._cache_key(task, prompt_version, model, system, user, schema)
        cached = self._cache_get(key)
        if cached is not None:
            meta = LLMMeta(task=task, model=model, prompt_version=prompt_version, status="cache_hit",
                           input_tokens=cached.get("input_tokens", 0), output_tokens=cached.get("output_tokens", 0))
            await self._record(meta, call_id, None)
            return schema.model_validate_json(cached["text"]), meta

        schema_retried = False
        for attempt in range(self.settings.llm_max_attempts):
            started = time.perf_counter()
            meta = LLMMeta(task=task, model=model, prompt_version=prompt_version)
            try:
                text, in_tok, out_tok, api_ms = await self._call_api(model, system, user, schema)
                meta.latency_ms = api_ms
                meta.input_tokens, meta.output_tokens = in_tok, out_tok
                meta.cost_usd = estimate_cost(model, in_tok, out_tok)
                obj = schema.model_validate_json(text)
            except (ValidationError, json.JSONDecodeError) as e:
                meta.status = "schema_error"
                await self._record(meta, call_id, str(e)[:500])
                if schema_retried:
                    raise LLMError(f"{model}: schema validation failed twice") from e
                schema_retried = True
                user = f"{user}\n\nYour previous answer did not match the JSON schema: {str(e)[:300]}. Return valid JSON only."
                continue
            except Exception as e:
                meta.latency_ms = int((time.perf_counter() - started) * 1000)
                meta.status = "error"
                await self._record(meta, call_id, self._describe_error(e))
                if not self._is_transient(e) or attempt == self.settings.llm_max_attempts - 1:
                    raise LLMError(f"{model}: {type(e).__name__}: {str(e)[:200]}") from e
                backoff = min(30.0, 2.0 * 2 ** attempt)
                await asyncio.sleep(max(backoff, self._server_retry_delay(e)) + random.uniform(0, 1))
                continue
            if schema_retried:
                meta.status = "schema_retry"
            await self._record(meta, call_id, None)
            self._cache_put(key, {"text": text, "input_tokens": in_tok, "output_tokens": out_tok})
            return obj, meta
        raise LLMError(f"{model}: exhausted retries")

    async def _call_api(self, model: str, system: str, user: str, schema: type[BaseModel]) -> tuple[str, int, int, int]:
        if self._client is None:
            raise LLMError("GEMINI_API_KEY is not set")
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.settings.llm_max_concurrency)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        async with self._sem:
            await self._respect_rpm(model)
            api_started = time.perf_counter()
            resp = await asyncio.wait_for(
                self._client.aio.models.generate_content(model=model, contents=user, config=config),
                timeout=self.settings.llm_timeout_s,
            )
        usage = resp.usage_metadata
        in_tok = (usage.prompt_token_count or 0) if usage else 0
        out_tok = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
        return resp.text or "", in_tok, out_tok, int((time.perf_counter() - api_started) * 1000)

    async def _respect_rpm(self, model: str) -> None:
        rpm = self.settings.llm_rpm
        if rpm <= 0:
            return
        window = self._recent.setdefault(model, [])
        while True:
            now = time.monotonic()
            window[:] = [t for t in window if now - t < 60]
            if len(window) < rpm:
                window.append(now)
                return
            await asyncio.sleep(60 - (now - window[0]) + 0.05)

    @staticmethod
    def _describe_error(e: Exception) -> str:
        text = str(e)
        quota = re.search(r"quotaId['\"]?:\s*['\"]([\w-]+)", text)
        retry = re.search(r"retry in ([\d.]+s)", text, re.I)
        parts = [type(e).__name__, str(getattr(e, "code", "")), quota.group(1) if quota else "", retry.group(1) if retry else ""]
        return " ".join(p for p in parts if p) + " | " + text[:200]

    @staticmethod
    def _server_retry_delay(e: Exception) -> float:
        m = re.search(r"retry in ([\d.]+)s", str(e), re.I)
        return min(float(m.group(1)), 65.0) if m else 0.0

    @staticmethod
    def _is_transient(e: Exception) -> bool:
        if isinstance(e, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        if not isinstance(e, genai_errors.APIError):
            return False
        if e.code == 429 and "PerDay" in str(e):
            return False
        return e.code in TRANSIENT_CODES

    async def _record(self, meta: LLMMeta, call_id: str | None, error: str | None) -> None:
        metrics.observe_llm(meta)
        if self.recorder:
            try:
                await self.recorder(meta, call_id, error)
            except Exception:
                pass


    @staticmethod
    def _cache_key(task, version, model, system, user, schema) -> str:
        blob = json.dumps([task, version, model, system, user, schema.model_json_schema()], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.settings.llm_cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        if not self.settings.llm_cache_enabled:
            return None
        path = self._cache_path(key)
        return json.loads(path.read_text()) if path.exists() else None

    def _cache_put(self, key: str, value: dict) -> None:
        if not self.settings.llm_cache_enabled:
            return
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
