"""Tests for secret scanning and redaction."""

from agent_knowledge.core import sanitizer


class TestScan:
    def test_detects_aws_key(self):
        findings = sanitizer.scan("my key is AKIAIOSFODNN7EXAMPLE")
        assert len(findings) == 1
        assert findings[0]["type"] == "AWS Access Key"

    def test_detects_api_key(self):
        findings = sanitizer.scan("api_key = sk-ant-abcdefghijklmnopqrstuvwxyz1234567890")
        assert len(findings) >= 1

    def test_detects_bearer_token(self):
        findings = sanitizer.scan("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc")
        assert len(findings) >= 1

    def test_detects_private_key(self):
        findings = sanitizer.scan("-----BEGIN PRIVATE KEY-----\nMIIEvgIBA...")
        assert len(findings) == 1
        assert findings[0]["type"] == "Private Key"

    def test_detects_connection_string(self):
        findings = sanitizer.scan("DATABASE_URL=postgres://user:pass@host:5432/db")
        assert len(findings) >= 1

    def test_detects_github_token(self):
        findings = sanitizer.scan("token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl")
        assert len(findings) >= 1

    def test_no_false_positive_on_normal_text(self):
        findings = sanitizer.scan("This is a normal sentence about coding patterns.")
        assert len(findings) == 0

    def test_no_false_positive_on_short_values(self):
        findings = sanitizer.scan("password = abc")
        assert len(findings) == 0


class TestRedact:
    def test_redacts_api_key(self):
        content = "Use api_key = sk-ant-abcdefghijklmnopqrstuvwxyz1234567890 for auth"
        redacted, findings = sanitizer.redact(content)
        assert "sk-ant-" not in redacted
        assert "[REDACTED:" in redacted
        assert len(findings) >= 1

    def test_redacts_multiple(self):
        content = "key=AKIAIOSFODNN7EXAMPLE and token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef"
        redacted, findings = sanitizer.redact(content)
        assert "AKIA" not in redacted
        assert "Bearer eyJ" not in redacted

    def test_preserves_clean_content(self):
        content = "# Auth Patterns\nUse mutex locks for token refresh."
        redacted, findings = sanitizer.redact(content)
        assert redacted == content
        assert len(findings) == 0


class TestContainsSecrets:
    def test_true_for_secrets(self):
        assert sanitizer.contains_secrets("key: AKIAIOSFODNN7EXAMPLE")

    def test_false_for_clean(self):
        assert not sanitizer.contains_secrets("Just normal text about patterns")
