"""Fail-closed secret hygiene checks for the PUBLIC BIRZHA repository.

The repository is intentionally public. This scanner therefore rejects likely
credential material in both the current tree and reachable Git history while
allowing non-secret resource identifiers and references to external secret
stores / GitHub Actions secrets.

Important: findings print only a rule name, object id and path. Secret values
are never printed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 2_000_000


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


RULES = (
    Rule("PRIVATE_KEY_PEM", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    Rule("GITHUB_CLASSIC_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    Rule("GITHUB_FINE_GRAINED_TOKEN", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    Rule("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    Rule("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    Rule("YANDEX_OAUTH_TOKEN", re.compile(r"\by0_[0-9A-Za-z_-]{20,}\b")),
    Rule("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")),
    Rule("URL_EMBEDDED_CREDENTIALS", re.compile(r"https?://[^\s/:@]{1,64}:[^\s/@]{8,}@")),
)

LITERAL_SECRET = re.compile(
    r"(?im)\b(?:password|passwd|secret|secret_key|secret_access_key|api_key|api_token|"
    r"access_token|auth_token|bearer_token|mcp_bearer_token|client_secret|private_key)\b"
    r"\s*[:=]\s*[\"']([^\"'\r\n]{12,})[\"']"
)

SAFE_LITERAL_MARKERS = (
    "${{",
    "${",
    "$(",
    "os.environ",
    "getenv",
    "process.env",
    "example",
    "placeholder",
    "dummy",
    "changeme",
    "not-a-real",
    "not_real",
    "redacted",
    "xxxxxxxx",
    "********",
    "<secret",
    "<token",
)

DANGEROUS_BASENAMES = {
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
    "service_account.json",
}
DANGEROUS_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".keystore"}


def _git(*args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )


def _dangerous_path(path: str) -> bool:
    p = Path(path)
    lower_name = p.name.lower()
    if lower_name == ".env.example":
        return False
    if lower_name in DANGEROUS_BASENAMES:
        return True
    return p.suffix.lower() in DANGEROUS_SUFFIXES


def _scan_text(text: str) -> set[str]:
    findings: set[str] = set()
    for rule in RULES:
        if rule.pattern.search(text):
            findings.add(rule.name)

    for match in LITERAL_SECRET.finditer(text):
        value = match.group(1).strip().lower()
        if not any(marker in value for marker in SAFE_LITERAL_MARKERS):
            findings.add("LITERAL_SECRET_ASSIGNMENT")
    return findings


def _decode_blob(raw: bytes) -> str | None:
    if len(raw) > MAX_BLOB_BYTES or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _current_tree_findings() -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    tracked = _git("ls-files").stdout.splitlines()
    for relative in tracked:
        if _dangerous_path(relative):
            findings.append(("DANGEROUS_CREDENTIAL_FILENAME", "WORKTREE", relative))
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > MAX_BLOB_BYTES:
            continue
        text = _decode_blob(path.read_bytes())
        if text is None:
            continue
        for rule in sorted(_scan_text(text)):
            findings.append((rule, "WORKTREE", relative))
    return findings


def _history_findings() -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    lines = _git("rev-list", "--objects", "--all").stdout.splitlines()
    seen: set[str] = set()

    for line in lines:
        object_id, _, relative = line.partition(" ")
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)

        obj_type = _git("cat-file", "-t", object_id).stdout.strip()
        if obj_type != "blob":
            continue

        path_label = relative or "<historical-blob>"
        if relative and _dangerous_path(relative):
            findings.append(("DANGEROUS_CREDENTIAL_FILENAME", object_id[:12], path_label))
            continue

        size = int(_git("cat-file", "-s", object_id).stdout.strip())
        if size > MAX_BLOB_BYTES:
            continue
        raw = _git("cat-file", "blob", object_id, text=False).stdout
        text = _decode_blob(raw)
        if text is None:
            continue
        for rule in sorted(_scan_text(text)):
            findings.append((rule, object_id[:12], path_label))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="scan every reachable Git blob")
    args = parser.parse_args()

    findings = _current_tree_findings()
    if args.history:
        findings.extend(_history_findings())

    unique = sorted(set(findings))
    print("PUBLIC_REPOSITORY_EXPECTED=true")
    print(f"PUBLIC_REPO_SECRET_FINDINGS={len(unique)}")
    if unique:
        print("PUBLIC_REPO_SECRET_SCAN=FAIL")
        for rule, object_id, path in unique:
            print(f"ERROR: rule={rule} object={object_id} path={path}")
        return 1

    print("PUBLIC_REPO_SECRET_SCAN=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
