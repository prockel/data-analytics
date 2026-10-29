"""Credential-safe live-probe behavior with offline synthetic responses."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from firstblood.api import APIError
from firstblood.auth import authentication_report
from firstblood.cli import main


class AuthenticationTests(unittest.TestCase):
    def test_proxy_binding_forwarded_unchanged_and_cache_not_trusted(self):
        binding = "opaque-synthetic-proxy-binding"
        client = Mock()
        client.get.return_value = {"queue": "RANKED_SOLO_5x5", "tier": "CHALLENGER",
                                   "entries": [{"puuid": "private-synthetic-player"}]}
        factory = Mock(return_value=client)
        report = authentication_report(binding, Path("unused"), "na1", client_factory=factory)
        factory.assert_called_once_with(binding, Path("unused"))
        client.get.assert_called_once_with("na1.api.riotgames.com",
            "/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5", cache=False)
        self.assertTrue(report["authentication_verified"])
        self.assertEqual(report["ladder_entry_count"], 1)
        self.assertNotIn(binding, json.dumps(report))
        self.assertNotIn("private-synthetic-player", json.dumps(report))

    def test_upstream_denial_does_not_claim_rejected_key(self):
        factory = Mock(side_effect=APIError("Safe upstream access message", kind="upstream_access_denied", status_code=403))
        report = authentication_report("opaque-test-binding", Path("unused"), "na1", client_factory=factory)
        self.assertTrue(report["credential_binding_present"])
        self.assertFalse(report["authentication_verified"])
        self.assertEqual(report["outcome"], "upstream_access_denied")

    def test_unexpected_success_schema_does_not_prove_authentication(self):
        client = Mock()
        client.get.return_value = {"some": "unrelated public response"}
        report = authentication_report("binding", Path("unused"), "na1", client_factory=Mock(return_value=client))
        self.assertFalse(report["authentication_verified"])
        self.assertEqual(report["outcome"], "unexpected_response")

    def test_missing_runtime_binding_has_specific_configuration_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            report = authentication_report("", Path(directory), "na1")
        self.assertFalse(report["credential_binding_present"])
        self.assertEqual(report["outcome"], "credential_binding_missing")
        self.assertIn("HTTPS secret", report["message"])

    def test_cli_writes_only_sanitized_report_and_preserves_failure_status(self):
        report = {"authentication_verified": False, "outcome": "upstream_access_denied"}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"authentication.json"
            capture = StringIO()
            with patch.dict(os.environ, {"RIOT_API_KEY": "private-synthetic-binding"}), \
                 patch("firstblood.auth.authentication_report", return_value=report) as probe, \
                 redirect_stdout(capture):
                status = main(["auth-check", "--output", str(output)])
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.read_text()), report)
            self.assertNotIn("private-synthetic-binding", capture.getvalue())
            self.assertEqual(probe.call_args.args[0], "private-synthetic-binding")
