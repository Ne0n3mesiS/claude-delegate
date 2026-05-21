#!/usr/bin/env python3
"""
PreToolUse hook — suggests /delegate when Bash command looks token-heavy.
Anti-spam: fires only once per session via /tmp sentinel.
Always exits 0 (suggests, never blocks).
"""
import json
import sys
from pathlib import Path

SENTINEL = Path("/tmp/.claude_delegate_hint_shown")

PATTERNS = [
    "grep -r",
    "grep -R",
    "find -name",
    "find . -",
    "cat *.csv",
    "cat *.json",
    "wc -l",
    "wc -c",
    "awk ",
    "sed ",
    "sort ",
    "jq ",
]


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = ""
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        command = tool_input

    if not command:
        sys.exit(0)

    matched = any(p in command for p in PATTERNS)
    if not matched:
        sys.exit(0)

    # Anti-spam: show hint only once per session
    if SENTINEL.exists():
        sys.exit(0)

    SENTINEL.touch()

    hint = (
        "💡 Cette commande pourrait être déléguée à un modèle local Ollama "
        "pour économiser des tokens Claude.\n"
        "Utilise /delegate ou `dg` pour router vers Qwen2.5:14b / DeepSeek-R1.\n"
        "Exemple : dg \"analyse ce fichier CSV et résume les colonnes\""
    )

    result = {
        "hookSpecificOutput": {
            "additionalContext": hint
        }
    }
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
