from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import requests

from firstblood.api import APIError, RiotClient


def response(code=200, body=None, headers=None):
    return Mock(status_code=code, headers=headers or {}, json=Mock(return_value=body or {"ok": True}))


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = Mock()
        self.sleeps = []
        self.client = RiotClient("synthetic-test-credential", Path(self.temp.name),
                                 attempts=3, session=self.session, sleep=self.sleeps.append)

    def test_rate_limit_and_transient_retries(self):
        self.session.get.side_effect = [response(429, headers={"Retry-After": "7"}), response(503), response()]
        self.assertEqual(self.client.get("na1.api.riotgames.com", "/test"), {"ok": True})
        self.assertEqual(self.session.get.call_count, 3)
        self.assertTrue(any(delay >= 7 for delay in self.sleeps))
        kwargs = self.session.get.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Riot-Token"], "synthetic-test-credential")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("verify", kwargs)  # requests' TLS verification remains enabled

    def test_cache_and_snapshot_isolation(self):
        self.session.get.return_value = response()
        for _ in range(2):
            self.client.get("na1.api.riotgames.com", "/test", snapshot="a")
        self.assertEqual(self.session.get.call_count, 1)
        self.client.get("na1.api.riotgames.com", "/test", snapshot="b")
        self.assertEqual(self.session.get.call_count, 2)

    def test_fail_fast_auth_without_key_leak(self):
        self.session.get.return_value = response(403)
        with self.assertRaisesRegex(APIError, "401/403") as caught:
            self.client.get("na1.api.riotgames.com", "/test")
        self.assertNotIn("synthetic-test-credential", str(caught.exception))
        self.assertEqual(self.session.get.call_count, 1)

    def test_network_retry_exhaustion(self):
        self.session.get.side_effect = requests.Timeout("sensitive upstream error")
        with self.assertRaisesRegex(APIError, "exhausted retries"):
            self.client.get("na1.api.riotgames.com", "/test")
        self.assertEqual(self.session.get.call_count, 3)

    def test_honor_consumed_rate_window(self):
        self.session.get.return_value = response(headers={"X-App-Rate-Limit": "1:120", "X-App-Rate-Limit-Count": "1:120"})
        self.client.get("na1.api.riotgames.com", "/one")
        self.client.get("na1.api.riotgames.com", "/two")
        self.assertTrue(any(delay > 119 for delay in self.sleeps))

    def test_untrusted_host_and_missing_key(self):
        with self.assertRaises(ValueError):
            self.client.get("untrusted.example", "/test")
        with self.assertRaises(APIError):
            RiotClient("", Path(self.temp.name))
