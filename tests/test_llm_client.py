"""LLM client reliability behaviour, tested with a fake API (no network)."""
import asyncio

from google.genai import errors as genai_errors
from pydantic import BaseModel

from app.llm import LLMClient, Prompt
from app.settings import Settings

P = Prompt(name="t", version="t.v1", system="sys", user="say $x")


class Out(BaseModel):
    word: str


def make_client(tmp_path, responses, monkeypatch):
    s = Settings(gemini_api_key="fake", llm_cache_dir=tmp_path, gemini_model_strong="primary",
                 gemini_model_fallback="backup", llm_max_attempts=2)
    client = LLMClient(s)
    calls = []

    async def fake_api(model, system, user, schema):
        calls.append(model)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, 10, 5, 7

    client._call_api = fake_api
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    return client, calls


async def _no_sleep(*_):
    return None


async def test_schema_retry_then_success_and_cache(tmp_path, monkeypatch):
    client, calls = make_client(tmp_path, ['{"nope": 1}', '{"word": "hi"}'], monkeypatch)
    out, meta = await client.generate(task="t", prompt=P, variables={"x": "a"}, schema=Out, tier="strong")
    assert out.word == "hi" and meta.status == "schema_retry" and calls == ["primary", "primary"]
    out2, meta2 = await client.generate(task="t", prompt=P, variables={"x": "a"}, schema=Out, tier="strong")
    assert out2.word == "hi" and meta2.status == "cache_hit" and len(calls) == 2


async def test_fallback_after_transient_errors(tmp_path, monkeypatch):
    err = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
    client, calls = make_client(tmp_path, [err, err, '{"word": "ok"}'], monkeypatch)
    out, meta = await client.generate(task="t", prompt=P, variables={"x": "b"}, schema=Out, tier="strong")
    assert out.word == "ok" and meta.status == "fallback" and calls == ["primary", "primary", "backup"]


async def test_daily_quota_skips_straight_to_fallback(tmp_path, monkeypatch):
    daily = genai_errors.ClientError(429, {"error": {"message": "quota GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})
    client, calls = make_client(tmp_path, [daily, '{"word": "ok"}'], monkeypatch)
    out, meta = await client.generate(task="t", prompt=P, variables={"x": "c"}, schema=Out, tier="strong")
    assert calls == ["primary", "backup"] and meta.status == "fallback"


async def test_rpm_limiter_waits_when_window_full(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, [], monkeypatch)
    client.settings.llm_rpm = 2
    waits = []

    async def fake_sleep(s):
        waits.append(s)
        client._recent["m"].clear()
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    for _ in range(3):
        await client._respect_rpm("m")
    assert len(waits) == 1
