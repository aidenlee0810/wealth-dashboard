"""
tests/test_corporate_actions.py — Alpaca corporate-actions ingest (§33, no network).

UNIT (pure parser):
  TestParse          forward/reverse split ratios + notes, dividends, bad-row drop
  TestFetchGuards    no keys / no symbols short-circuit (no network)
  TestEnvCreds       env + native APCA names + absent

INTEGRATION (in-memory sqlite, real corporate_actions DDL):
  TestSync           idempotent upsert, split/dividend counts
  TestTrailing       trailing-12mo dividend window math

Run:
    python -m pytest tests/test_corporate_actions.py -v
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "jobs"))

import corporate_actions as ca  # noqa: E402

# corporate_actions DDL mirrored from migrations/001_cloud_initial.sql
_DDL = """
CREATE TABLE corporate_actions (
    ticker        TEXT NOT NULL,
    ex_date       TEXT NOT NULL,
    action_type   TEXT NOT NULL CHECK(action_type IN ('split','dividend','merger','spinoff','rights')),
    split_ratio   REAL,
    dividend      REAL,
    notes         TEXT,
    source        TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, ex_date, action_type)
);
"""

SAMPLE = {"corporate_actions": {
    "forward_splits": [{"symbol": "NVDA", "new_rate": 10, "old_rate": 1, "ex_date": "2024-06-10"}],
    "reverse_splits": [{"symbol": "XYZ", "new_rate": 1, "old_rate": 10, "ex_date": "2023-05-01"}],
    "cash_dividends": [
        {"symbol": "AAPL", "rate": 0.25, "ex_date": "2024-05-10", "special": False},
        {"symbol": "COST", "rate": 15.0, "ex_date": "2024-01-12", "special": True},
        {"symbol": "BAD",  "rate": None, "ex_date": "2024-01-01"},          # dropped
        {"symbol": "",     "rate": 1.0,  "ex_date": "2024-01-01"},          # dropped (no symbol)
    ],
}, "next_page_token": None}


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


# ────────────────────────────────────────────────────────────────────────────
class TestParse(unittest.TestCase):
    def test_forward_split(self):
        rows = ca.parse_corporate_actions(SAMPLE)
        nv = next(r for r in rows if r["ticker"] == "NVDA")
        self.assertEqual(nv["action_type"], "split")
        self.assertEqual(nv["split_ratio"], 10.0)
        self.assertEqual(nv["notes"], "1:10")

    def test_reverse_split(self):
        rows = ca.parse_corporate_actions(SAMPLE)
        xy = next(r for r in rows if r["ticker"] == "XYZ")
        self.assertAlmostEqual(xy["split_ratio"], 0.1)
        self.assertEqual(xy["notes"], "10:1")

    def test_dividends_and_special(self):
        rows = ca.parse_corporate_actions(SAMPLE)
        divs = [r for r in rows if r["action_type"] == "dividend"]
        self.assertEqual(len(divs), 2)                          # BAD + empty dropped
        cost = next(r for r in divs if r["ticker"] == "COST")
        self.assertEqual(cost["notes"], "special")
        self.assertEqual(cost["dividend"], 15.0)

    def test_bare_payload_and_nondict(self):
        bare = {"forward_splits": [{"symbol": "T", "new_rate": 2, "old_rate": 1, "ex_date": "2024-01-01"}]}
        self.assertEqual(len(ca.parse_corporate_actions(bare)), 1)
        self.assertEqual(ca.parse_corporate_actions(None), [])
        self.assertEqual(ca.parse_corporate_actions([1, 2]), [])

    def test_zero_old_rate_dropped(self):
        bad = {"corporate_actions": {"forward_splits": [
            {"symbol": "Z", "new_rate": 5, "old_rate": 0, "ex_date": "2024-01-01"}]}}
        self.assertEqual(ca.parse_corporate_actions(bad), [])


# ────────────────────────────────────────────────────────────────────────────
class TestFetchGuards(unittest.TestCase):
    def test_no_keys(self):
        self.assertEqual(ca.fetch_corporate_actions(["AAPL"], "", "", "2024-01-01", "2024-12-31"), [])

    def test_no_symbols(self):
        self.assertEqual(ca.fetch_corporate_actions([], "k", "s", "2024-01-01", "2024-12-31"), [])


# ────────────────────────────────────────────────────────────────────────────
class TestEnvCreds(unittest.TestCase):
    def setUp(self):
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

    def test_primary_names(self):
        os.environ["ALPACA_KEY"] = "k"
        os.environ["ALPACA_SECRET"] = "s"
        self.assertEqual(ca._env_creds(), ("k", "s"))

    def test_native_names(self):
        os.environ["APCA_API_KEY_ID"] = "k2"
        os.environ["APCA_API_SECRET_KEY"] = "s2"
        self.assertEqual(ca._env_creds(), ("k2", "s2"))

    def test_absent(self):
        self.assertEqual(ca._env_creds(), (None, None))

    def test_sync_universe_skips_without_keys(self):
        out = ca.sync_universe(_conn(), ["AAPL"], "2026-05-30")
        self.assertEqual(out["skipped"], "no_alpaca_key")


# ────────────────────────────────────────────────────────────────────────────
class TestSync(unittest.TestCase):
    def test_idempotent_upsert(self):
        conn = _conn()
        rows = ca.parse_corporate_actions(SAMPLE)
        s1 = ca.sync(conn, rows)
        self.assertEqual(s1["new"], 4)           # 2 splits + 2 dividends
        self.assertEqual(s1["splits_new"], 2)
        self.assertEqual(s1["dividends_new"], 2)
        # second run adds nothing (INSERT OR IGNORE on PK)
        s2 = ca.sync(conn, rows)
        self.assertEqual(s2["new"], 0)
        total = conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
        self.assertEqual(total, 4)

    def test_split_and_dividend_same_exdate_coexist(self):
        conn = _conn()
        rows = [
            {"ticker": "AAA", "ex_date": "2024-02-02", "action_type": "split",
             "split_ratio": 2.0, "dividend": None, "notes": "1:2", "source": "alpaca"},
            {"ticker": "AAA", "ex_date": "2024-02-02", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.5, "notes": None, "source": "alpaca"},
        ]
        s = ca.sync(conn, rows)
        self.assertEqual(s["new"], 2)            # different action_type → both kept


# ────────────────────────────────────────────────────────────────────────────
class TestTrailing(unittest.TestCase):
    def test_ttm_window(self):
        conn = _conn()
        rows = [
            {"ticker": "KO", "ex_date": "2025-09-14", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.485, "notes": None, "source": "x"},
            {"ticker": "KO", "ex_date": "2025-06-14", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.485, "notes": None, "source": "x"},
            {"ticker": "KO", "ex_date": "2025-03-14", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.485, "notes": None, "source": "x"},
            {"ticker": "KO", "ex_date": "2024-12-14", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.485, "notes": None, "source": "x"},
            # >12mo before asof → excluded
            {"ticker": "KO", "ex_date": "2024-01-14", "action_type": "dividend",
             "split_ratio": None, "dividend": 0.46, "notes": None, "source": "x"},
        ]
        ca.sync(conn, rows)
        ttm = ca.trailing_dividend(conn, "KO", "2025-12-01")
        self.assertAlmostEqual(ttm, 0.485 * 4, places=4)   # 4 quarters, old one excluded

    def test_ttm_zero_when_none(self):
        self.assertEqual(ca.trailing_dividend(_conn(), "NONE", "2025-12-01"), 0.0)


if __name__ == "__main__":
    unittest.main()
