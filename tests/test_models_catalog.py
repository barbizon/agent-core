"""The models each backend offers, for the model menu."""

import json

from agent_core import models_catalog


def test_codex_models_come_from_its_own_list_in_its_order(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "b", "display_name": "B", "visibility": "list", "priority": 2, "description": "second"},
        {"slug": "hidden", "display_name": "H", "visibility": "hide", "priority": 0},
        {"slug": "a", "display_name": "A", "visibility": "list", "priority": 1},
        {"display_name": "no slug", "visibility": "list"},
    ]}))
    assert [m["id"] for m in models_catalog.codex_models(cache)] == ["a", "b"]
    assert models_catalog.codex_models(cache)[1] == {"id": "b", "label": "B", "note": "b"}


def test_no_codex_list_means_none(tmp_path):
    assert models_catalog.codex_models(tmp_path / "missing.json") == []
    (tmp_path / "bad.json").write_text("{broken")
    assert models_catalog.codex_models(tmp_path / "bad.json") == []


def test_claude_offers_its_current_models_by_full_name():
    from pathlib import Path
    assert [m["id"] for m in models_catalog.available(codex_cache=Path("/none"))["claude"]] == \
        ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]



def test_the_default_is_named(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "second", "visibility": "list", "priority": 2},
        {"slug": "first", "visibility": "list", "priority": 1}]}))
    assert models_catalog.default_model("codex", cache) == "first"
    assert models_catalog.default_model("claude", cache) == "claude-opus-5-5"
    assert models_catalog.default_model("codex", tmp_path / "none.json") is None
