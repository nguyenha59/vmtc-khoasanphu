"""Inventory source risks without printing credential values or reading .env/data.

Only Git-visible source files are inspected. This is a bounded inventory, not a
replacement for a dedicated secret scanner or a complete Git history review.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "security-audit" / "source-scan.json"
PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "openai_project_key": re.compile(r"sk-proj-[A-Za-z0-9_-]{30,}"),
    "private_key_header": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{call_name(node.value)}.{node.attr}"
    return "?"


def main() -> None:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    paths = sorted(set(filter(None, listed.stdout.split("\0"))))
    credential_patterns, sensitive_paths, source_sinks = [], [], []
    files_scanned = 0
    for relative in paths:
        path = ROOT / relative
        if relative == ".env" or relative == "config/users.yaml" or path.suffix.lower() in {".db", ".sqlite", ".pkl", ".pem", ".key"}:
            sensitive_paths.append(relative)
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".example"}:
            continue
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        files_scanned += 1
        for category, regex in PATTERNS.items():
            for match in regex.finditer(text):
                credential_patterns.append({"path": relative, "line": text.count("\n", 0, match.start()) + 1,
                                            "category": category})
        if path.suffix != ".py" or not relative.startswith(("api/", "core/")):
            continue
        for node in ast.walk(ast.parse(text, filename=relative)):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node.func)
            if name in {"eval", "exec", "pickle.load", "pickle.loads", "os.system"}:
                source_sinks.append({"path": relative, "line": node.lineno, "call": name})
            if name.startswith("subprocess.") and any(k.arg == "shell" and isinstance(k.value, ast.Constant)
                                                       and k.value.value is True for k in node.keywords):
                source_sinks.append({"path": relative, "line": node.lineno, "call": name, "shell": True})
    report = {"scope": "Git-visible current source; excludes ignored .env/data and full history",
              "files_scanned": files_scanned, "credential_pattern_hits": credential_patterns,
              "git_visible_sensitive_paths": sensitive_paths, "source_sinks_requiring_review": source_sinks}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
