#!/usr/bin/env python3
"""Tripwire for the patterns a malicious contribution would need.

This is not a general linter. It fails CI when the source tree gains:

  * a network host outside the allowlist (the player talks to TIDAL only),
  * a dynamic-execution or deserialisation primitive (eval, exec, pickle, ...),
  * a shell-invoking subprocess call,
  * a long base64-looking literal or a run of hex escapes (encoded payloads).

Every hit prints file:line so a reviewer can look. A hit that is legitimate
can be marked with `# tripwire: allow` on the same line — that marker is
itself visible in the diff, which is the point.

Run from the repository root: python .github/scripts/tripwire.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ticli"
TESTS = SRC / "tests"

ALLOW_MARK = "tripwire: allow"

# Hosts the application is allowed to contact. Subdomains are allowed.
ALLOWED_HOSTS = ("tidal.com",)
# Tests may additionally use loopback servers and fake hosts that cannot
# resolve on the real internet: single-label names (`http://cdn/...`) and
# the RFC 2606/6761 reserved TLDs.
TEST_ALLOWED_HOSTS = ("127.0.0.1", "localhost")
TEST_ALLOWED_TLDS = (".example", ".test", ".invalid", ".localhost")

URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")

# (regex, explanation). Applied to application code only.
FORBIDDEN_IN_APP = [
    (r"\beval\s*\(", "eval()"),
    (r"\bexec\s*\(", "exec()"),
    (r"(?<![\w.])compile\s*\(", "compile() (re.compile is fine)"),
    (r"\b__import__\s*\(", "__import__()"),
    (r"\bimportlib\b", "importlib (dynamic import)"),
    (r"\bmarshal\b", "marshal"),
    (r"\bpickle\b", "pickle (unsafe deserialisation)"),
    (r"\bbase64\b", "base64"),
    (r"\bcodecs\.(decode|encode)\b", "codecs.decode/encode"),
    (r"\bzlib\b", "zlib"),
    (r"\bctypes\b", "ctypes"),
    (r"\bos\.system\s*\(", "os.system()"),
    (r"\bos\.(popen|spawn\w*|exec\w*)\s*\(", "os.popen/spawn/exec"),
    (r"\bshell\s*=\s*True\b", "subprocess with shell=True"),
    (r"\b(socket\.create_connection|http\.client|urllib\.request|smtplib|ftplib|paramiko|telnetlib)\b",
     "raw network client (all traffic should go through tidalapi/requests to TIDAL)"),
]

# Applied to tests as well: a test has no business doing these either.
FORBIDDEN_EVERYWHERE = [
    (r"\bos\.system\s*\(", "os.system()"),
    (r"\bshell\s*=\s*True\b", "subprocess with shell=True"),
    (r"\bbase64\.b64decode\s*\(", "base64.b64decode()"),
]

LONG_BLOB_RE = re.compile(r"['\"][A-Za-z0-9+/=]{80,}['\"]")
HEX_RUN_RE = re.compile(r"(\\x[0-9a-fA-F]{2}){6,}")
# Six or more consecutive \x escapes is a payload, not an ANSI key constant.


def host_allowed(host: str, is_test: bool) -> bool:
    host = host.lower().rstrip(".")
    if is_test and ("." not in host or host.endswith(TEST_ALLOWED_TLDS)):
        return True
    allowed = ALLOWED_HOSTS + (TEST_ALLOWED_HOSTS if is_test else ())
    return any(host == a or host.endswith("." + a) for a in allowed)


def scan(path: Path) -> list[str]:
    is_test = TESTS in path.parents
    rules = FORBIDDEN_EVERYWHERE if is_test else FORBIDDEN_IN_APP + FORBIDDEN_EVERYWHERE
    problems: list[str] = []
    rel = path.relative_to(ROOT)
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for host in URL_RE.findall(line):
            if not host_allowed(host, is_test):
                problems.append(f"{rel}:{lineno}: network host not on allowlist: {host}")
        for pattern, why in rules:
            if re.search(pattern, line):
                problems.append(f"{rel}:{lineno}: {why}")
        if LONG_BLOB_RE.search(line):
            problems.append(f"{rel}:{lineno}: long base64-like string literal")
        if HEX_RUN_RE.search(line):
            problems.append(f"{rel}:{lineno}: run of hex escapes")
    return problems


def main() -> int:
    files = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
    problems = [p for f in files for p in scan(f)]
    if problems:
        print("tripwire: suspicious patterns found\n")
        print("\n".join(problems))
        print(f"\n{len(problems)} hit(s). Review each one; mark a legitimate use "
              f"with `# {ALLOW_MARK}` on that line.")
        return 1
    print(f"tripwire: clean ({len(files)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
