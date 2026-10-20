"""
core/ai_provider.py — which AI provider writes the test cases (not only Anthropic)
=================================================================================
QAX's AI features were tied to one provider: no Anthropic credit = no test cases. This
module picks the provider from the environment and gives every caller the same client
shape (`client.messages.create(model=, max_tokens=, system=, messages=)` → a response
with `.content[0].text`, `.stop_reason`, `.usage`):

    QAX_AI_PROVIDER=anthropic   (default)  ANTHROPIC_API_KEY
    QAX_AI_PROVIDER=openai                 any OpenAI-compatible /chat/completions API:
        QAX_AI_BASE_URL   https://api.openai.com/v1 (default) · Azure OpenAI · a local
                          model server: Ollama http://localhost:11434/v1, LM Studio, vLLM
        QAX_AI_API_KEY    the key (OPENAI_API_KEY also read; a local server needs none)
        QAX_AI_MODEL      the model name (required: e.g. gpt-4o, llama3.1:70b)

The scan's deterministic probes, reading the app and running tests use no AI at all.
A local model keeps the story and code on this machine. Never logs keys.
"""

from __future__ import annotations

import os
from types import SimpleNamespace


def provider() -> str:
    """'anthropic' (default) or 'openai' (any OpenAI-compatible API)."""
    p = os.getenv("QAX_AI_PROVIDER", "anthropic").strip().lower()
    return "openai" if p in ("openai", "openai-compatible", "ollama", "azure", "local") else "anthropic"


def ai_configured() -> bool:
    """True when the chosen provider has what it needs to make a call."""
    if provider() == "openai":
        return bool(os.getenv("QAX_AI_MODEL", "").strip())
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def describe() -> str:
    """One line for doctor / logs: which provider and model (never the key)."""
    if provider() == "openai":
        return f"OpenAI-compatible · {os.getenv('QAX_AI_BASE_URL', 'https://api.openai.com/v1')} · " \
               f"model {os.getenv('QAX_AI_MODEL', '(not set)')}"
    return "Anthropic"


def make_ai_client(api_key: "str | None" = None):
    """The client for the configured provider (Anthropic SDK client, or the compatible one)."""
    if provider() == "openai":
        return OpenAICompatClient()
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


class _Messages:
    """`client.messages` — the Anthropic-shaped call, sent as an OpenAI chat completion."""

    def __init__(self, outer: "OpenAICompatClient") -> None:
        """Keep the client (URL, key, model)."""
        self._c = outer

    @staticmethod
    def _content(content):
        """Anthropic content (a string or blocks) → OpenAI content."""
        if isinstance(content, str):
            return content
        parts = []
        for b in content or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "image" and isinstance(b.get("source"), dict):
                src = b["source"]
                parts.append({"type": "image_url", "image_url": {
                    "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}})
        if all(p["type"] == "text" for p in parts):
            return "\n".join(p["text"] for p in parts)
        return parts

    def create(self, *, model: str = "", max_tokens: int = 4096, system=None, messages=None,
               temperature=None, **_ignored):
        """Send the request; return an Anthropic-shaped response. Raises on an HTTP error."""
        import requests
        msgs = [dict(m) for m in (messages or [])]
        prefill = ""
        if msgs and msgs[-1].get("role") == "assistant":
            # Anthropic "prefill" (answer starts with "[") has no OpenAI equivalent: ask for it
            prefill = str(msgs.pop()["content"] or "")
            if msgs and msgs[-1].get("role") == "user" and prefill:
                msgs[-1]["content"] = f"{self._content(msgs[-1]['content'])}\n\nStart your answer with: {prefill}"
        out = []
        if system:
            sys_text = system if isinstance(system, str) else "\n".join(
                b.get("text", "") for b in system if isinstance(b, dict))
            out.append({"role": "system", "content": sys_text})
        out += [{"role": m.get("role", "user"), "content": self._content(m.get("content"))} for m in msgs]
        body = {"model": self._c.model or model, "messages": out, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"Content-Type": "application/json"}
        if self._c.api_key:
            if os.getenv("QAX_AI_AZURE", "").lower() in ("1", "true", "yes"):
                headers["api-key"] = self._c.api_key            # Azure OpenAI's header
            else:
                headers["Authorization"] = f"Bearer {self._c.api_key}"
        # never follow a redirect: the key would travel to wherever it points
        r = requests.post(f"{self._c.base_url}/chat/completions", json=body, headers=headers, timeout=300,
                          allow_redirects=False)
        if 300 <= r.status_code < 400:
            raise RuntimeError(f"Error code: {r.status_code} - the AI endpoint redirected; set QAX_AI_BASE_URL to the final URL")
        if r.status_code >= 400:
            raise RuntimeError(f"Error code: {r.status_code} - {r.text[:300]}")
        data = r.json() or {}
        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content")) or ""
        usage = data.get("usage") or {}
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="max_tokens" if choice.get("finish_reason") == "length" else "end_turn",
            usage=SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0),
                                  output_tokens=usage.get("completion_tokens", 0)),
            model=data.get("model", body["model"]))


class OpenAICompatClient:
    """An OpenAI-compatible chat API behind the Anthropic client shape QAX uses."""

    def __init__(self) -> None:
        """Read QAX_AI_BASE_URL / QAX_AI_API_KEY (or OPENAI_API_KEY) / QAX_AI_MODEL."""
        self.base_url = os.getenv("QAX_AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        from urllib.parse import urlsplit
        u = urlsplit(self.base_url)
        if u.scheme != "https" and (u.hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            # a story, code and the key over plain http to another machine: refuse
            raise RuntimeError("QAX_AI_BASE_URL must be https (plain http only for a model on this machine)")
        self.api_key = os.getenv("QAX_AI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("QAX_AI_MODEL", "").strip()
        self.messages = _Messages(self)
