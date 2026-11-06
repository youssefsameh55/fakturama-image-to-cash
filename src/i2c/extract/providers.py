"""LLM providers for extraction. Each returns the raw order dict; parsing/validation is the service's job."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from i2c.errors import ConfigError, ProviderError
from i2c.extract import prompt
from i2c.extract.schema import TOOL_NAME, tool_definition

log = logging.getLogger(__name__)


# --- image preprocessing ---------------------------------------------------------------

@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    media_type: str
    sha256: str          # hash of the ORIGINAL file bytes (cache key)
    width: int
    height: int

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")


def prepare_image(path: Path, max_long_side: int = 1568) -> PreparedImage:
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        fmt = (img.format or "").upper()
        w, h = img.size
        scale = max_long_side / max(w, h)
        if scale < 1 or fmt not in ("PNG", "JPEG"):
            if scale < 1:
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            buf = io.BytesIO()
            if fmt == "JPEG":
                img.convert("RGB").save(buf, "JPEG", quality=90)
                media = "image/jpeg"
            else:
                img.save(buf, "PNG", optimize=True)
                media = "image/png"
            return PreparedImage(buf.getvalue(), media, sha, *img.size)
        return PreparedImage(raw, "image/png" if fmt == "PNG" else "image/jpeg", sha, w, h)


# --- provider interface ----------------------------------------------------------------

@dataclass
class ProviderResult:
    data: dict
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class ExtractionProvider(Protocol):
    name: str
    model: str

    def extract(self, image: PreparedImage, feedback: str | None = None) -> ProviderResult: ...


# --- Anthropic -------------------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 4096) -> None:
        import anthropic

        self._anthropic = anthropic
        # api_key=None lets the SDK resolve credentials itself (env var / `ant auth login` profile).
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self._forced_tool_supported = True

    def _request(self, content: list[dict], forced: bool):
        tool_choice = {"type": "tool", "name": TOOL_NAME} if forced else {"type": "auto"}
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=prompt.SYSTEM,
            tools=[tool_definition()],
            tool_choice=tool_choice,
            messages=[{"role": "user", "content": content}],
        )

    def extract(self, image: PreparedImage, feedback: str | None = None) -> ProviderResult:
        a = self._anthropic
        text = prompt.user_instruction(use_tool=True)
        if feedback:
            text += "\n\n" + feedback
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": image.media_type, "data": image.b64}},
            {"type": "text", "text": text},
        ]
        t0 = time.perf_counter()
        try:
            try:
                resp = self._request(content, forced=self._forced_tool_supported)
            except a.BadRequestError as exc:
                # Some models (e.g. Opus 5.5 / Sonnet 5.5) reject forced tool_choice; fall back to auto.
                if self._forced_tool_supported and "tool_choice" in str(exc):
                    log.info("model %s rejects forced tool_choice; retrying with auto", self.model)
                    self._forced_tool_supported = False
                    resp = self._request(content, forced=False)
                else:
                    raise
        except a.AuthenticationError as exc:
            raise ProviderError("Anthropic authentication failed: check ANTHROPIC_API_KEY") from exc
        except a.NotFoundError as exc:
            raise ProviderError(f"Anthropic model not found: {self.model}") from exc
        except a.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit hit (after SDK retries)") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ProviderError(f"cannot reach the Anthropic API: {exc}") from exc
        except TypeError as exc:
            if "authentication" not in str(exc):
                raise
            raise ProviderError("no Anthropic credentials: set ANTHROPIC_API_KEY in .env") from exc
        latency = time.perf_counter() - t0

        if resp.stop_reason == "refusal":
            raise ProviderError("the model declined the request (stop_reason=refusal)")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("response hit max_tokens; raise extraction.max_tokens in settings.yaml")
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
        if block is None:
            raise ProviderError(f"model did not call {TOOL_NAME} (stop_reason={resp.stop_reason})")
        data = block.input if isinstance(block.input, dict) else json.loads(block.input)
        return ProviderResult(data, resp.usage.input_tokens, resp.usage.output_tokens, latency)


# --- Gemini ----------------------------------------------------------------------------

class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None, max_tokens: int = 4096) -> None:
        if not api_key:
            raise ConfigError("GEMINI_API_KEY is not set")
        from google import genai
        from google.genai import types

        self._types = types
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def extract(self, image: PreparedImage, feedback: str | None = None) -> ProviderResult:
        types = self._types
        text = prompt.user_instruction(use_tool=False)
        if feedback:
            text += "\n\n" + feedback
        t0 = time.perf_counter()
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=image.data, mime_type=image.media_type), text],
                config=types.GenerateContentConfig(
                    system_instruction=prompt.SYSTEM,
                    response_mime_type="application/json",
                    max_output_tokens=self.max_tokens,
                ),
            )
        except Exception as exc:  # google-genai raises its own error hierarchy
            raise ProviderError(f"Gemini API error: {exc}") from exc
        latency = time.perf_counter() - t0
        raw = (resp.text or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gemini returned invalid JSON: {raw[:200]!r}") from exc
        if not isinstance(data, dict):
            raise ProviderError("Gemini returned JSON that is not an object")
        usage = getattr(resp, "usage_metadata", None)
        return ProviderResult(
            data,
            getattr(usage, "prompt_token_count", 0) or 0,
            getattr(usage, "candidates_token_count", 0) or 0,
            latency,
        )


def make_provider(settings) -> ExtractionProvider:
    max_tokens = int(settings.get("extraction.max_tokens", 4096))
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings.anthropic_model, settings.anthropic_api_key, max_tokens)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings.gemini_model, settings.gemini_api_key, max_tokens)
    raise ConfigError(f"unknown LLM provider {settings.llm_provider!r}")
