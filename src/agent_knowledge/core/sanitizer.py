"""Content sanitizer — detect and redact secrets before storing."""

from __future__ import annotations

import re

# Patterns that match common secret formats
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*[A-Za-z0-9/+=]{40}")),
    ("Generic API Key", re.compile(r"(?i)(api[_\-]?key|apikey)\s*[=:]\s*['\"]?[A-Za-z0-9\-_]{20,}['\"]?")),
    ("Generic Secret", re.compile(r"(?i)(secret|token|password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{8,}['\"]?")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{20,}")),
    ("Private Key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("Connection String", re.compile(r"(?i)(postgres|mysql|mongodb|redis|sqlite)://[^\s]{10,}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("Anthropic API Key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("OpenAI API Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
]


def scan(content: str) -> list[dict]:
    """Scan content for secret patterns. Returns list of findings."""
    findings = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            findings.append({
                "type": name,
                "match": match.group()[:20] + "...",
                "position": match.start(),
            })
    return findings


def redact(content: str) -> tuple[str, list[dict]]:
    """Redact secrets from content. Returns (redacted_content, findings)."""
    findings = []
    redacted = content
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            findings.append({
                "type": name,
                "match": match.group()[:20] + "...",
                "position": match.start(),
            })
        redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
    return redacted, findings


def contains_secrets(content: str) -> bool:
    """Quick check if content contains any secret patterns."""
    return any(pattern.search(content) for _, pattern in SECRET_PATTERNS)
