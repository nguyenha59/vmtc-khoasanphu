"""Run the bounded security review with isolated synthetic configuration.

python scripts/run_security_review.py
python scripts/run_security_review.py --browser   # also launches a local headless browser

A zero test exit code does NOT mean no vulnerabilities: known missing controls
are reported as strict XFAIL. See the security review documents for conditions.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "test_security_audit_review.py", "test_security_attack_matrix.py", "test_security_http_review.py",
    "test_security_fixes.py", "test_security_hardening.py", "test_privacy_masking.py", "test_safe_files.py",
    "test_m5_triage.py", "test_m7_config_quality.py", "test_red_workflow.py", "test_ai_fixes.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    output = ROOT / "data" / "security-audit"
    output.mkdir(parents=True, exist_ok=True)
    # Keep this owned directory for evidence; no project/runtime data is deleted.
    work = Path(tempfile.mkdtemp(prefix="runtime-", dir=output)).resolve()
    assert work.is_relative_to(output.resolve())
    env = dict(os.environ)
    env.update(APP_DB_URL=f"sqlite:///{(work / 'isolated.db').as_posix()}", APP_DATA_DIR=str(work / "data"),
               APP_USERS_FILE=str(ROOT / "config" / "users.example.yaml"), APP_ENV="development",
               APP_COOKIE_SECURE="0", APP_SECRET_KEY=Fernet.generate_key().decode(),
               SESSION_SECRET_KEY=Fernet.generate_key().decode(), LLM_PROVIDER="mock",
               QUESTION_SELECTOR_PROVIDER="mock", APP_JOBS="0", OPENAI_API_KEY="",
               TEAMS_WEBHOOK_URL="", SMS_TEST_API_URL="", RUN_SECURITY_BROWSER="1" if args.browser else "0")
    tests = [str(ROOT / "tests" / name) for name in TESTS]
    if args.browser:
        tests.append(str(ROOT / "tests" / "test_security_browser_review.py"))
    result = subprocess.run([sys.executable, "-m", "pytest", *tests, "-ra", "--tb=short",
                             f"--junitxml={output / 'expanded-results.xml'}"], cwd=ROOT, env=env)
    print("Known XFAILs are unresolved security controls, not successful security checks.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
