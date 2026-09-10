"""Gemini client wrapper: retries, usage accounting, manual function-calling.

Manual loop on purpose: we append the model's exact Content objects back into
history so Gemini 3 `thoughtSignature` parts survive across tool-use turns.
Dropping them is a known cause of degraded/hallucinatory multi-step behavior.
"""
import time

from google import genai
from google.genai import errors, types

from . import config


class Usage:
    """Accumulates token usage and estimated cost across calls."""

    def __init__(self):
        self.prompt = 0
        self.output = 0          # candidates + thoughts
        self.calls = 0
        self.cost = 0.0
        self.last_prompt = 0     # prompt size of most recent call (context gauge)

    def add(self, model: str, meta) -> None:
        if meta is None:
            return
        p = meta.prompt_token_count or 0
        c = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
        self.prompt += p
        self.output += c
        self.calls += 1
        self.last_prompt = p
        pin, pout = config.PRICING.get(model, config.DEFAULT_PRICE)
        self.cost += p * pin / 1e6 + c * pout / 1e6

    def summary(self) -> str:
        return (f"{self.calls} calls, {self.prompt:,} in / {self.output:,} out tokens, "
                f"~${self.cost:.4f} (last context: {self.last_prompt:,} tokens)")


class LLM:
    def __init__(self, usage: Usage | None = None):
        self.client = genai.Client(api_key=config.api_key())
        self.usage = usage or Usage()

    def generate(self, model: str, contents, system: str | None = None,
                 tools: list[types.FunctionDeclaration] | None = None,
                 temperature: float | None = None):
        """generate_content with retries on transient errors."""
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            tools=[types.Tool(function_declarations=tools)] if tools else None,
        )
        delay = 2.0
        for attempt in range(5):
            try:
                resp = self.client.models.generate_content(
                    model=model, contents=contents, config=cfg)
                self.usage.add(model, resp.usage_metadata)
                return resp
            except errors.APIError as e:
                transient = getattr(e, "code", None) in (429, 500, 503)
                if not transient or attempt == 4:
                    raise
                time.sleep(delay)
                delay *= 2

    def text(self, model: str, prompt: str, system: str | None = None) -> str:
        """Simple no-tools text call (used for compaction/worker plumbing)."""
        resp = self.generate(model, prompt, system=system)
        return resp.text or ""
