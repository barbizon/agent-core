"""Which AI models each backend offers, for choosing one (the Mac app's model menu).

* **Claude:** the current models by name and version, as full model ids (what
  ``claude --model`` accepts), so you see exactly what answers. Update this list
  when a new Claude model comes out; "Default" is whatever your subscription's
  Claude Code uses.
* **Codex:** read from Codex's own model list, ``~/.codex/models_cache.json``
  (only the models Codex itself shows, in its order). Only names and labels are
  read; nothing else in the file.
* **OpenAI API:** a few common ones; any name can still be typed in the settings.

"Default" (no model) means the backend picks. ``default_model`` says which one
that is, so it can be named instead of just saying "default":

* **Claude:** what ``claude -p`` (started the way Orwick starts it, without your
  Claude Code settings) reports in its startup event: Claude Opus 5.5 when this
  was checked (2026-11-12). Update it with ``CLAUDE_MODELS`` when Claude changes.
* **Codex:** the first model of Codex's own list (priority 1); checked against
  what ``codex exec --ignore-user-config`` reports (gpt-6.1-sol).
"""

import json
from pathlib import Path
from typing import Any

CODEX_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"

CLAUDE_MODELS = [
    {"id": "claude-fable-5-2", "label": "Claude Fable 5.2", "note": "claude-fable-5-2"},
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "note": "claude-opus-5-5"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "note": "claude-sonnet-5-5"},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "note": "claude-haiku-5-5"},
]
CLAUDE_DEFAULT = "claude-opus-5-5"

# Short names some people type; shown under the full name they stand for.
CLAUDE_ALIASES = {"fable": "Claude Fable 5.2", "opus": "Claude Opus 5.5", "sonnet": "Claude Sonnet 5.5",
                  "haiku": "Claude Haiku 5.5"}
OPENAI_MODELS = [
    {"id": "gpt-4.1", "label": "GPT-4.1", "note": ""},
    {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "note": "Orwick's default for this backend"},
]


def codex_models(path: Path = CODEX_MODELS_CACHE) -> list[dict[str, str]]:
    """The models Codex lists, best first; empty if Codex hasn't cached its list."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    shown = [m for m in models or [] if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    shown.sort(key=lambda m: m.get("priority") if isinstance(m.get("priority"), int) else 999)
    return [{"id": str(m["slug"]), "label": str(m.get("display_name") or m["slug"]),
             "note": str(m["slug"])} for m in shown]


def available(codex_cache: Path = CODEX_MODELS_CACHE) -> dict[str, list[dict[str, Any]]]:
    return {"claude": CLAUDE_MODELS, "codex": codex_models(codex_cache), "openai": OPENAI_MODELS}


def default_model(backend: str, codex_cache: Path = CODEX_MODELS_CACHE) -> str | None:
    """The model a backend uses when none is chosen (None if it can't be known)."""
    if backend == "claude":
        return CLAUDE_DEFAULT
    if backend == "codex":
        models = codex_models(codex_cache)
        return models[0]["id"] if models else None
    if backend == "openai":
        return "gpt-4.1-mini"
    return None


def defaults(codex_cache: Path = CODEX_MODELS_CACHE) -> dict[str, str | None]:
    return {backend: default_model(backend, codex_cache) for backend in ("claude", "codex", "openai")}
