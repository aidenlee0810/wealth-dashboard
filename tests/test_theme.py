"""
tests/test_theme.py — Phase 10 tests: model_registry + theme lifecycle view.

Covers:
  TestModelRegistry (11 tests)  — register, promote, retire, shadow stats, list
  TestRegistryView   (4 tests)  — build_registry_view shape
  TestMigration009   (3 tests)  — schema migration creates table + seed rows
  TestThemeViewData  (5 tests)  — latest_sector_theme.json structure asserts
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ── import path ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _in_memory_db():
    """Create an in-memory SQLite DB with migration 009 applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "migrations" / "009_cloud_model_registry.sql").read_text()
    conn.executescript(sql)
    return conn


# ═══════════════════════════════════════════════════════════════════════════════
# TestModelRegistry
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelRegistry(unittest.TestCase):

    def setUp(self):
        self.conn = _in_memory_db()
        import model_registry as mr
        self.mr = mr

    def tearDown(self):
        self.conn.close()

    # ── register ──────────────────────────────────────────────────────────────

    def test_register_inserts_row(self):
        mr = self.mr
        mid = mr.register(self.conn, "test_model_v1.0", "test_family", "1.0",
                          config={"key": "val"}, description="test")
        self.assertEqual(mid, "test_model_v1.0")
        row = self.conn.execute(
            "SELECT * FROM model_registry WHERE model_id=?", (mid,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["family"], "test_family")
        self.assertEqual(row["version"], "1.0")
        self.assertEqual(row["status"], "experimental")

    def test_register_is_idempotent(self):
        """Calling register twice must not raise or duplicate."""
        mr = self.mr
        mr.register(self.conn, "idempotent_v1", "fam", "1")
        mr.register(self.conn, "idempotent_v1", "fam", "1")  # second call
        count = self.conn.execute(
            "SELECT COUNT(*) FROM model_registry WHERE model_id='idempotent_v1'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_register_stores_config_json(self):
        mr = self.mr
        config = {"weight_bq": 0.25, "regime_boost": 5.0}
        mr.register(self.conn, "cfg_model_v1", "scorer", "1.0", config=config)
        row = self.conn.execute(
            "SELECT config_json FROM model_registry WHERE model_id='cfg_model_v1'"
        ).fetchone()
        stored = json.loads(row["config_json"])
        self.assertEqual(stored["weight_bq"], 0.25)

    # ── status transitions ───────────────────────────────────────────────────

    def test_set_status_shadow(self):
        mr = self.mr
        mr.register(self.conn, "shadow_v1", "scorer", "1")
        mr.set_status(self.conn, "shadow_v1", "shadow")
        row = self.conn.execute(
            "SELECT status FROM model_registry WHERE model_id='shadow_v1'"
        ).fetchone()
        self.assertEqual(row["status"], "shadow")

    def test_set_status_invalid_raises(self):
        mr = self.mr
        mr.register(self.conn, "bad_v1", "scorer", "1")
        with self.assertRaises(ValueError):
            mr.set_status(self.conn, "bad_v1", "INVALID_STATUS")

    # ── promote ───────────────────────────────────────────────────────────────

    def test_promote_advances_to_production(self):
        mr = self.mr
        mr.register(self.conn, "new_prod_v2", "scorer", "2.0")
        mr.promote(self.conn, "scorer", "new_prod_v2")
        row = self.conn.execute(
            "SELECT status, promoted_at FROM model_registry WHERE model_id='new_prod_v2'"
        ).fetchone()
        self.assertEqual(row["status"], "production")
        self.assertIsNotNone(row["promoted_at"])

    def test_promote_retires_previous_production(self):
        """Old production model must become deprecated on promotion."""
        mr = self.mr
        # Seed has candidate_scorer_v4.5 as production — use that
        mr.register(self.conn, "scorer_v5.0", "candidate_scorer", "5.0")
        mr.promote(self.conn, "candidate_scorer", "scorer_v5.0")

        old = self.conn.execute(
            "SELECT status FROM model_registry WHERE model_id='candidate_scorer_v4.5'"
        ).fetchone()
        self.assertEqual(old["status"], "deprecated")

    def test_promote_same_model_is_safe(self):
        """Promoting the already-production model must not break anything."""
        mr = self.mr
        mr.promote(self.conn, "candidate_scorer", "candidate_scorer_v4.5")
        row = self.conn.execute(
            "SELECT status FROM model_registry WHERE model_id='candidate_scorer_v4.5'"
        ).fetchone()
        self.assertEqual(row["status"], "production")

    # ── retire ────────────────────────────────────────────────────────────────

    def test_retire_sets_deprecated(self):
        mr = self.mr
        mr.register(self.conn, "retire_v1", "scorer", "1")
        mr.retire(self.conn, "retire_v1")
        row = self.conn.execute(
            "SELECT status, retired_at FROM model_registry WHERE model_id='retire_v1'"
        ).fetchone()
        self.assertEqual(row["status"], "deprecated")
        self.assertIsNotNone(row["retired_at"])

    # ── shadow stats ──────────────────────────────────────────────────────────

    def test_update_shadow_stats(self):
        mr = self.mr
        mr.register(self.conn, "shadow_stat_v1", "scorer", "1")
        mr.update_shadow_stats(self.conn, "shadow_stat_v1", {
            "period_start": "2026-01-01",
            "period_end":   "2026-02-28",
            "signal_count": 142,
            "hit_rate":     0.63,
            "ev":           0.028,
            "vs_production_correlation": 0.87,
            "graduation_decision": "pending",
        })
        row = self.conn.execute(
            "SELECT * FROM model_registry WHERE model_id='shadow_stat_v1'"
        ).fetchone()
        self.assertEqual(row["shadow_signal_count"], 142)
        self.assertAlmostEqual(row["shadow_hit_rate"], 0.63)
        self.assertEqual(row["graduation_decision"], "pending")

    # ── get_production ────────────────────────────────────────────────────────

    def test_get_production_returns_correct_family(self):
        mr = self.mr
        prod = mr.get_production(self.conn, "candidate_scorer")
        self.assertIsNotNone(prod)
        self.assertEqual(prod["model_id"], "candidate_scorer_v4.5")
        self.assertEqual(prod["status"], "production")
        # config should be parsed
        self.assertIsInstance(prod.get("config"), dict)

    def test_get_production_missing_family_returns_none(self):
        mr = self.mr
        prod = mr.get_production(self.conn, "nonexistent_family")
        self.assertIsNone(prod)


# ═══════════════════════════════════════════════════════════════════════════════
# TestRegistryView
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistryView(unittest.TestCase):

    def setUp(self):
        self.conn = _in_memory_db()
        import model_registry as mr
        self.mr = mr

    def tearDown(self):
        self.conn.close()

    def test_build_registry_view_keys(self):
        view = self.mr.build_registry_view(self.conn)
        for key in ("production", "shadow", "experimental", "deprecated", "total"):
            self.assertIn(key, view)

    def test_production_list_non_empty(self):
        view = self.mr.build_registry_view(self.conn)
        # 4 seed rows are all production
        self.assertGreaterEqual(len(view["production"]), 4)

    def test_total_equals_sum_of_buckets(self):
        view = self.mr.build_registry_view(self.conn)
        bucket_sum = (len(view["production"]) + len(view["shadow"]) +
                      len(view["experimental"]) + len(view["deprecated"]))
        self.assertEqual(view["total"], bucket_sum)

    def test_no_config_json_in_view(self):
        """config_json blob must be stripped; parsed config may be present."""
        view = self.mr.build_registry_view(self.conn)
        for model in view["production"]:
            self.assertNotIn("config_json", model)


# ═══════════════════════════════════════════════════════════════════════════════
# TestMigration009
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigration009(unittest.TestCase):

    def test_migration_creates_table(self):
        conn = _in_memory_db()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("model_registry", tables)
        conn.close()

    def test_seed_rows_present(self):
        conn = _in_memory_db()
        count = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        # 4 seed rows: candidate_scorer, regime_classifier, risk_governor, factor_model
        self.assertEqual(count, 4)
        conn.close()

    def test_seed_status_all_production(self):
        conn = _in_memory_db()
        rows = conn.execute(
            "SELECT status FROM model_registry"
        ).fetchall()
        for row in rows:
            self.assertEqual(row[0], "production")
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# TestThemeViewData  — validate the structure of latest_sector_theme.json
# ═══════════════════════════════════════════════════════════════════════════════

class TestThemeViewData(unittest.TestCase):
    """
    Validates the JSON structure that theme-discovery.js will consume.
    If the view file doesn't exist (fresh repo), tests are skipped gracefully.
    """

    VIEW_PATH = ROOT / "data" / "views" / "latest_sector_theme.json"
    REQUIRED_THEME_KEYS = {"name", "type", "phase", "leadership_score",
                           "ret1m", "ret3m", "ret6m", "trend_score",
                           "rel_strength"}
    VALID_PHASES = {"emerging", "leading", "extended", "neutral", "fading", "breakdown"}

    def _load(self):
        if not self.VIEW_PATH.exists():
            self.skipTest("latest_sector_theme.json not present (run snapshot first)")
        with open(self.VIEW_PATH) as f:
            return json.load(f)

    def test_view_has_themes_key(self):
        data = self._load()
        self.assertIn("themes", data)
        self.assertIsInstance(data["themes"], list)

    def test_theme_entries_have_required_keys(self):
        data = self._load()
        theme_entries = [t for t in data["themes"] if t.get("type") == "theme"]
        self.assertGreater(len(theme_entries), 0, "No themes in view")
        for t in theme_entries:
            missing = self.REQUIRED_THEME_KEYS - set(t.keys())
            self.assertEqual(missing, set(), f"Theme '{t.get('name')}' missing keys: {missing}")

    def test_all_phases_are_valid(self):
        data = self._load()
        for t in data["themes"]:
            if t.get("type") == "theme" and t.get("phase"):
                self.assertIn(
                    t["phase"], self.VALID_PHASES,
                    f"Theme '{t.get('name')}' has invalid phase '{t['phase']}'"
                )

    def test_leadership_score_in_range(self):
        data = self._load()
        for t in data["themes"]:
            if t.get("type") == "theme" and t.get("leadership_score") is not None:
                self.assertGreaterEqual(t["leadership_score"], 0)
                self.assertLessEqual(t["leadership_score"], 100)

    def test_snapshot_date_present(self):
        data = self._load()
        self.assertIn("snapshot_date", data)
        self.assertIsNotNone(data["snapshot_date"])


if __name__ == "__main__":
    unittest.main()
