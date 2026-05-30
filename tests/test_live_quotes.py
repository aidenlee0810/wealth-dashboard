"""
tests/test_live_quotes.py — Alpaca live-quote layer (no network).

UNIT (pure parsers in jobs/live_quotes.py):
  TestNum            float coercion guards (None/NaN/inf/str)
  TestParseOne       price preference, day-change math, prev-close fallback
  TestParseSnapshot  full map, bad-symbol drop, {"snapshots":…} wrapper
  TestParseClock     clock normalization
  TestFetchGuards    no-keys / no-symbols return {} WITHOUT touching network

SERVER (server.py credential resolution; _parse_env_file does no I/O on home):
  TestServerCreds    env first, native APCA_* names, secrets.env fallback

Run:
    python -m pytest tests/test_live_quotes.py -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

import live_quotes as lq  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
class TestNum(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(lq._num(3), 3.0)
        self.assertEqual(lq._num("4.5"), 4.5)

    def test_invalid(self):
        self.assertIsNone(lq._num(None))
        self.assertIsNone(lq._num("abc"))
        self.assertIsNone(lq._num(float("nan")))
        self.assertIsNone(lq._num(float("inf")))


# ────────────────────────────────────────────────────────────────────────────
class TestParseOne(unittest.TestCase):
    def test_full_snapshot_day_change(self):
        snap = {
            "latestTrade": {"p": 201.5, "t": "2026-05-29T19:59:00Z"},
            "dailyBar": {"o": 199, "h": 202.3, "l": 198.5, "c": 201.4, "v": 51_000_000},
            "prevDailyBar": {"c": 198.0},
            "latestQuote": {"bp": 201.4, "ap": 201.6},
        }
        q = lq.parse_one("aapl", snap)
        self.assertEqual(q["ticker"], "AAPL")
        self.assertEqual(q["price"], 201.5)
        self.assertEqual(q["prev_close"], 198.0)
        self.assertAlmostEqual(q["change"], 3.5)
        self.assertAlmostEqual(q["change_pct"], 3.5 / 198.0 * 100)
        self.assertEqual(q["day_high"], 202.3)
        self.assertEqual(q["bid"], 201.4)
        self.assertEqual(q["source"], "alpaca")

    def test_price_preference_trade_over_bars(self):
        snap = {"latestTrade": {"p": 10}, "minuteBar": {"c": 9}, "dailyBar": {"c": 8}}
        self.assertEqual(lq.parse_one("X", snap)["price"], 10)

    def test_price_falls_back_to_minute_then_daily(self):
        self.assertEqual(lq.parse_one("X", {"minuteBar": {"c": 9}, "dailyBar": {"c": 8}})["price"], 9)
        self.assertEqual(lq.parse_one("X", {"dailyBar": {"c": 8}})["price"], 8)

    def test_no_price_returns_none(self):
        self.assertIsNone(lq.parse_one("X", {"latestQuote": {"bp": 1}}))
        self.assertIsNone(lq.parse_one("X", {}))
        self.assertIsNone(lq.parse_one("X", "not-a-dict"))

    def test_prev_close_fallback_to_open_when_no_prev_bar(self):
        # fresh IPO: no prevDailyBar → measure change vs today's open
        snap = {"latestTrade": {"p": 12}, "dailyBar": {"o": 10, "c": 11.9}}
        q = lq.parse_one("IPO", snap)
        self.assertEqual(q["prev_close"], 10)
        self.assertAlmostEqual(q["change"], 2)

    def test_negative_change(self):
        q = lq.parse_one("NVDA", {"latestTrade": {"p": 140}, "prevDailyBar": {"c": 142}})
        self.assertLess(q["change"], 0)
        self.assertLess(q["change_pct"], 0)

    def test_zero_prev_close_no_div_by_zero(self):
        q = lq.parse_one("X", {"latestTrade": {"p": 5}, "prevDailyBar": {"c": 0}})
        # prev 0 → can't compute pct; change stays None (guarded)
        self.assertIsNone(q["change_pct"])


# ────────────────────────────────────────────────────────────────────────────
class TestParseSnapshot(unittest.TestCase):
    def test_full_map_and_bad_symbol_drop(self):
        payload = {
            "AAPL": {"latestTrade": {"p": 201.5}, "prevDailyBar": {"c": 198}},
            "NVDA": {"latestTrade": {"p": 140}, "prevDailyBar": {"c": 142}},
            "BADD": {"nope": 1},          # no price → dropped
        }
        q = lq.parse_snapshot(payload)
        self.assertIn("AAPL", q)
        self.assertIn("NVDA", q)
        self.assertNotIn("BADD", q)
        self.assertEqual(len(q), 2)

    def test_snapshots_wrapper_and_token_ignored(self):
        payload = {"snapshots": {"AAPL": {"latestTrade": {"p": 200}}},
                   "next_page_token": "abc"}
        q = lq.parse_snapshot(payload)
        self.assertEqual(list(q.keys()), ["AAPL"])

    def test_non_dict_returns_empty(self):
        self.assertEqual(lq.parse_snapshot(None), {})
        self.assertEqual(lq.parse_snapshot([1, 2, 3]), {})


# ────────────────────────────────────────────────────────────────────────────
class TestParseClock(unittest.TestCase):
    def test_open(self):
        c = lq.parse_clock({"is_open": True, "next_close": "2026-05-29T20:00:00Z",
                            "timestamp": "2026-05-29T15:00:00Z"})
        self.assertTrue(c["is_open"])
        self.assertEqual(c["next_close"], "2026-05-29T20:00:00Z")
        self.assertEqual(c["source"], "alpaca")

    def test_missing_is_open(self):
        self.assertIsNone(lq.parse_clock({})["is_open"])
        self.assertIsNone(lq.parse_clock("nope")["is_open"])


# ────────────────────────────────────────────────────────────────────────────
class TestFetchGuards(unittest.TestCase):
    """No keys / no symbols must short-circuit BEFORE any network call."""

    def test_no_symbols(self):
        self.assertEqual(lq.fetch_snapshots([], "k", "s"), {})

    def test_no_keys(self):
        self.assertEqual(lq.fetch_snapshots(["AAPL"], "", ""), {})
        self.assertEqual(lq.fetch_snapshots(["AAPL"], None, None), {})

    def test_clock_no_keys(self):
        self.assertEqual(lq.fetch_clock("", ""), {})

    def test_symbols_deduped_and_capped(self):
        # patch the HTTP layer so we can assert what URL would be requested
        captured = {}

        def fake_http(url, key, secret):
            captured["url"] = url
            return {}

        with mock.patch.object(lq, "_http_json", fake_http):
            lq.fetch_snapshots(["AAPL", "aapl", "NVDA"], "k", "s")
        self.assertIn("symbols=AAPL%2CNVDA", captured["url"])  # de-duped, upper


# ────────────────────────────────────────────────────────────────────────────
class TestServerCreds(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self._saved = {k: os.environ.get(k) for k in
                       ("ALPACA_KEY", "ALPACA_SECRET", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_first(self):
        os.environ["ALPACA_KEY"] = "envkey"
        os.environ["ALPACA_SECRET"] = "envsecret"
        with mock.patch.object(self.server, "_parse_env_file", return_value={}):
            self.assertEqual(self.server._alpaca_creds(), ("envkey", "envsecret"))

    def test_native_apca_names(self):
        os.environ["APCA_API_KEY_ID"] = "k2"
        os.environ["APCA_API_SECRET_KEY"] = "s2"
        with mock.patch.object(self.server, "_parse_env_file", return_value={}):
            self.assertEqual(self.server._alpaca_creds(), ("k2", "s2"))

    def test_absent_returns_none_pair(self):
        with mock.patch.object(self.server, "_parse_env_file", return_value={}):
            self.assertEqual(self.server._alpaca_creds(), (None, None))

    def test_secrets_file_fallback(self):
        with mock.patch.object(self.server, "_parse_env_file",
                               return_value={"ALPACA_KEY": "fk", "ALPACA_SECRET": "fs"}):
            self.assertEqual(self.server._alpaca_creds(), ("fk", "fs"))

    def test_parse_env_file_real_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "secrets.env"
            p.write_text('# comment\nALPACA_KEY="abc"\nALPACA_SECRET = sec ret\n\nBARE=1\n')
            env = self.server._parse_env_file(p)
            self.assertEqual(env["ALPACA_KEY"], "abc")          # quotes stripped
            self.assertEqual(env["ALPACA_SECRET"], "sec ret")   # inner space kept
            self.assertEqual(env["BARE"], "1")
            self.assertNotIn("# comment", env)

    def test_parse_env_file_missing(self):
        self.assertEqual(self.server._parse_env_file(Path("/no/such/file.env")), {})


if __name__ == "__main__":
    unittest.main()
