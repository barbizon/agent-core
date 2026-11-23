"""Skill contracts: what a skill may create, checked before anything is saved.

A skill may ship ``contract.json`` next to its SKILL.md. The tool that writes the
skill's docs checks every doc against it *before* the approval prompt, so a doc in
the wrong shape is never shown for approval nor saved; the AI gets the list of
problems and fixes them. The contract is the skill's promise about its output, so
the app (and you) can rely on the shape.

    {
      "format": 1,
      "only_these_docs": true,          # no doc names outside "docs"
      "docs": {
        "flows": {
          "title": "Flows",             # the tab's label in the app
          "required": true,             # must exist once written (in this call or before)
          "diagrams": {"flowchart": 1}, # at least N Mermaid blocks of each type
          "tables": [["#", "Flow", "Trigger", "What it achieves"]],  # tables with these columns
          "sections": {"pattern": "^### \\d+\\. ", "min": 1, "diagram": "sequenceDiagram"},
          "max_chars": 60000
        }
      }
    }

``sections``: lines matching ``pattern`` start a section; at least ``min`` of them,
and each must hold a Mermaid block of type ``diagram``.
"""

import json
import re
from pathlib import Path
from typing import Any

CONTRACT_FILE = "contract.json"
MERMAID = re.compile(r"^```mermaid[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def load(skill_folder: Path) -> dict[str, Any] | None:
    """The skill's contract, or None when it has none. A broken contract is an error:
    a skill that promises a shape must keep it readable."""
    path = Path(skill_folder) / CONTRACT_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("docs"), dict):
        raise ValueError(f"{path}: a contract needs a \"docs\" object")
    return data


def doc_order(contract: dict[str, Any]) -> list[str]:
    return list(contract.get("docs", {}))


def diagram_types(text: str) -> list[str]:
    """The type of every Mermaid block in the text (flowchart, sequenceDiagram, ...)."""
    types = []
    for block in MERMAID.findall(text):
        for line in block.splitlines():
            words = line.split()
            if words and not words[0].startswith("%%"):
                kind = words[0]
                types.append("flowchart" if kind == "graph" else kind)
                break
    return types


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_headers(text: str) -> list[list[str]]:
    """The header row of every Markdown table (a | row followed by a |---| row)."""
    lines = text.splitlines()
    headers = []
    for i in range(len(lines) - 1):
        first, rule = lines[i].strip(), lines[i + 1].strip()
        if first.startswith("|") and rule.startswith("|") and set(rule) <= set("|-: "):
            headers.append(_cells(first))
    return headers


def _norm(cells: list[str]) -> list[str]:
    return [" ".join(c.casefold().split()) for c in cells]


def check_doc(name: str, text: str, rules: dict[str, Any]) -> list[str]:
    problems = []
    limit = rules.get("max_chars")
    if limit and len(text) > limit:
        problems.append(f"{name}: at most {limit:,} characters (has {len(text):,})")
    found = diagram_types(text)
    for kind, least in (rules.get("diagrams") or {}).items():
        if found.count(kind) < least:
            problems.append(f"{name}: needs at least {least} Mermaid {kind} diagram"
                            f"{'s' if least > 1 else ''} (```mermaid block starting with {kind})")
    headers = [_norm(h) for h in table_headers(text)]
    for columns in rules.get("tables") or []:
        if _norm(columns) not in headers:
            problems.append(f"{name}: needs a table with exactly these columns: | " + " | ".join(columns) + " |")
    sections = rules.get("sections")
    if sections:
        pattern = re.compile(sections["pattern"])
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if pattern.match(line)]
        if len(starts) < sections.get("min", 1):
            problems.append(f"{name}: needs at least {sections.get('min', 1)} section heading(s) matching "
                            f"{sections['pattern']}")
        wanted = sections.get("diagram")
        for n, start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            body = "\n".join(lines[start:end]) + "\n"
            if wanted and wanted not in diagram_types(body):
                problems.append(f"{name}: section “{lines[start].lstrip('#').strip()}” needs a Mermaid {wanted}")
    return problems


def check(contract: dict[str, Any], docs: dict[str, str], existing: set[str]) -> list[str]:
    """Every way these docs break the contract (empty: they keep it). ``existing``: the
    docs already saved, which count for "required"."""
    rules = contract.get("docs", {})
    problems = []
    if contract.get("only_these_docs"):
        for name in docs:
            if name not in rules:
                problems.append(f"{name}: not a doc of this skill; allowed: {', '.join(rules)}")
    for name, text in docs.items():
        if name in rules:
            problems += check_doc(name, text, rules[name] or {})
    for name, doc_rules in rules.items():
        if (doc_rules or {}).get("required") and name not in docs and name not in existing:
            problems.append(f"{name}: required (write it in this call)")
    return problems
