"""
tests/test_data_separation.py — Cloud/Local separation verification (Plan §18)

Test categories:
  UNIT  (always run, no external deps):
    test_6_validate_and_query_rejects_personal_unit
    test_7_sql_injection_patterns_rejected

  CONDITIONAL (run if precondition met, skip with clear reason if not):
    test_1_cloud_db_no_personal_tables     → skip if DB not initialized
    test_2_git_status_no_personal_files    → skip if git not available
    test_3_gha_workflows_no_personal_refs  → skip if no .github/workflows yet
    test_4_jobs_no_personal_db_refs        → skip if jobs/ dir not populated
    test_5_api_db_query_rejects_personal   → skip if server not running

Expected results (Phase 1.1, server NOT running):
  PASS  test_1  (DB initialized)
  PASS  test_2  (no personal files staged)
  SKIP  test_3  [EXPECTED: .github/workflows not created until Phase 3]
  SKIP  test_4  [EXPECTED: jobs/*.py files not yet present]
  SKIP  test_5  [EXPECTED: server not running in unit-test environment]
  PASS  test_6  (pure unit test — no DB or server needed)
  PASS  test_7  (pure unit test — no DB or server needed)

  Result: 4 PASS · 3 SKIP · 0 FAIL  ← target

Run:
    python -m pytest tests/test_data_separation.py -v
    python -m pytest tests/test_data_separation.py -v -k "unit"   # unit tests only
"""

import os
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

# Allow tests to import project modules from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLOUD_DB_PATH = PROJECT_ROOT / "data" / "db" / "research_market.sqlite"

PERSONAL_TABLES = db.PERSONAL_TABLES  # canonical list from db.py

PERSONAL_FILE_PATTERNS = [
    r'personal\.sqlite',
    r'personal\.sqlite-(journal|shm|wal)',
    r'user_actions.*\.(csv|json|sqlite)',
    r'portfolio_snapshots.*\.(csv|json|sqlite)',
    r'account_buckets.*\.(csv|json|sqlite)',
    r'tax_lots.*\.(csv|json|sqlite)',
    r'data/personal/',
    r'\.wealth-dashboard/',
]


class TestDataSeparation(unittest.TestCase):
    """Cloud/Local data separation tests.

    Tests are ordered from most fundamental (unit) to most integrated (live server).
    Always run unit tests first; conditional tests skip with descriptive reasons.
    """

    # ── UNIT: no external dependencies ──────────────────────────────────────

    def test_6_validate_and_query_rejects_personal_unit(self):
        """UNIT: validate_and_query() must reject all 7 personal tables with HTTP 403.

        This is a pure unit test — no DB file or running server required.
        It tests the in-memory allowlist enforcement in db.py.
        """
        for table in sorted(PERSONAL_TABLES):
            with self.subTest(table=table):
                with self.assertRaises(db.QueryError) as ctx:
                    db.validate_and_query(table, limit=1)
                self.assertEqual(
                    403, ctx.exception.status,
                    f"Expected HTTP 403 for personal table '{table}', "
                    f"got {ctx.exception.status}. "
                    f"Check PERSONAL_TABLES and validate_and_query() in db.py."
                )

    def test_7_sql_injection_patterns_rejected(self):
        """UNIT: Common SQL injection patterns must raise QueryError.

        Tests that no user-controlled string is interpolated directly into SQL.
        All queries use parameterized placeholders.
        """
        injection_attempts = [
            # (table, order_by) — malformed table or order_by with SQL payloads
            ("ticker_master; DROP TABLE prices_daily--", None),
            ("ticker_master", "DROP TABLE prices_daily"),
            ("ticker_master", "ticker; DROP TABLE prices_daily"),
            ("ticker_master OR 1=1", None),
            ("ticker_master", "1; --"),
            ("'; SELECT * FROM user_actions; --", None),
        ]

        for table, order_by in injection_attempts:
            with self.subTest(table=table, order_by=order_by):
                with self.assertRaises(db.QueryError,
                        msg=f"Expected QueryError for injection: table={table!r} order_by={order_by!r}"):
                    db.validate_and_query(table, order_by=order_by, limit=1)

    # ── CONDITIONAL: require DB file ─────────────────────────────────────────

    def test_1_cloud_db_no_personal_tables(self):
        """CONDITIONAL: Cloud DB must not contain any personal tables.

        Skip reason when not initialized:
          Run `python db.py init` to create the cloud DB, then re-run this test.
        """
        if not CLOUD_DB_PATH.exists():
            self.skipTest(
                "Cloud DB not initialized — run `python db.py init` first. "
                f"Expected path: {CLOUD_DB_PATH}"
            )

        # db.py-level guard
        try:
            db.assert_no_personal_tables_in_cloud()
        except RuntimeError as e:
            self.fail(
                f"SECURITY: personal table found in cloud DB: {e}. "
                "Drop these tables from research_market.sqlite immediately."
            )

        # Direct SQL double-check
        conn = sqlite3.connect(f"file:{CLOUD_DB_PATH}?mode=ro", uri=True)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        leaks = tables & PERSONAL_TABLES
        self.assertEqual(
            set(), leaks,
            f"Cloud DB has personal tables: {sorted(leaks)}. "
            "These must only exist in ~/.wealth-dashboard/personal.sqlite."
        )

    # ── CONDITIONAL: require git ──────────────────────────────────────────────

    def test_2_git_status_no_personal_files(self):
        """CONDITIONAL: No personal data files should appear in git status.

        Skip reason when git unavailable:
          This test requires `git` to be installed and project to be a git repo.
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True,
                cwd=str(PROJECT_ROOT), timeout=10,
            )
        except FileNotFoundError:
            self.skipTest("git not found in PATH — cannot check staged files")
        except subprocess.SubprocessError as e:
            self.skipTest(f"git command failed: {e}")

        offenders = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            filename = line[3:].strip()
            for pat in PERSONAL_FILE_PATTERNS:
                if re.search(pat, filename):
                    offenders.append((filename, pat))
                    break

        self.assertEqual(
            [], offenders,
            f"Personal data files detected in git status: {offenders}. "
            "Use `git reset HEAD <file>` to unstage them."
        )

    # ── CONDITIONAL: require .github/workflows/ ──────────────────────────────

    def test_3_gha_workflows_no_personal_refs(self):
        """CONDITIONAL: GHA workflows must never reference personal data.

        Skip reason when workflows don't exist yet:
          GitHub Actions workflows are created in Phase 3 (daily snapshot).
          This test will become active then. Expected SKIP in Phase 1/2.
        """
        wf_dir = PROJECT_ROOT / ".github" / "workflows"
        if not wf_dir.exists():
            self.skipTest(
                "No .github/workflows/ directory yet — "
                "expected SKIP in Phase 1/2 (workflows added in Phase 3)."
            )

        workflow_files = list(wf_dir.glob("*.yml"))
        if not workflow_files:
            self.skipTest(
                ".github/workflows/ directory exists but is empty — "
                "expected SKIP until Phase 3 workflow is created."
            )

        forbidden_keywords = [
            "personal.sqlite",
            "user_actions",
            "portfolio_snapshots",
            "tax_lots",
            "account_buckets",
            "~/.wealth-dashboard",
            "/api/local/",
        ]

        offenders = []
        for wf in workflow_files:
            content = wf.read_text()
            for kw in forbidden_keywords:
                if kw in content:
                    offenders.append((wf.name, kw))

        self.assertEqual(
            [], offenders,
            f"GHA workflows reference personal data: {offenders}. "
            "Personal data must never flow through GitHub Actions."
        )

    # ── CONDITIONAL: require jobs/*.py files ─────────────────────────────────

    def test_4_jobs_no_personal_db_refs(self):
        """CONDITIONAL: Snapshot/build jobs must use cloud DB only.

        Skip reason when jobs/ is empty:
          Phase 1 jobs/ only contains _logging.py and migrate_rec_log.py.
          The main snapshot job (daily_snapshot.py) is added in Phase 3.
          Expected SKIP until then.

        Files explicitly allowed to reference local DB:
          - migrate_rec_log.py (writes to cloud, but may inspect user's export)
          - _logging.py (helper, not a snapshot job)
        """
        jobs_dir = PROJECT_ROOT / "jobs"
        if not jobs_dir.exists():
            self.skipTest("No jobs/ directory found.")

        # Files that are allowed to reference local DB paths
        LOCAL_OK_JOBS = {"migrate_rec_log.py"}
        HELPER_PREFIX = "_"

        job_py_files = [
            f for f in jobs_dir.glob("*.py")
            if f.name not in LOCAL_OK_JOBS and not f.name.startswith(HELPER_PREFIX)
        ]

        if not job_py_files:
            self.skipTest(
                "No non-helper job files found in jobs/ — "
                "expected SKIP in Phase 1/2 (daily_snapshot.py added in Phase 3)."
            )

        forbidden_keywords = [
            "personal.sqlite",
            "~/.wealth-dashboard",
            "db.connect_local(",
            "db.local(",
            "from db import local",
        ]

        offenders = []
        for job in job_py_files:
            content = job.read_text()
            for kw in forbidden_keywords:
                if kw in content:
                    offenders.append((job.name, kw))

        self.assertEqual(
            [], offenders,
            f"Job files reference personal DB: {offenders}. "
            "Snapshot jobs must only access the cloud DB."
        )

    # ── CONDITIONAL: require running server ──────────────────────────────────

    def test_5_api_db_query_rejects_personal(self):
        """CONDITIONAL: /api/db/query must return 403 for all personal tables.

        Skip reason when server not running:
          Start the server with `python server.py` and re-run this test.
          In CI/unit-test environments this is ALWAYS an expected SKIP.
        """
        import urllib.request
        import urllib.error

        server_url = "http://localhost:5500"
        try:
            with urllib.request.urlopen(f"{server_url}/api/db/health", timeout=2):
                pass
        except Exception:
            self.skipTest(
                "Server not running (http://localhost:5500 unreachable). "
                "Start `python server.py` and re-run for live endpoint verification. "
                "EXPECTED SKIP in unit-test / CI environments."
            )

        for table in sorted(PERSONAL_TABLES):
            with self.subTest(table=table):
                url = f"{server_url}/api/db/query?table={table}&limit=1"
                try:
                    urllib.request.urlopen(url, timeout=3)
                    self.fail(
                        f"/api/db/query returned 200 for personal table '{table}'. "
                        "This is a SECURITY VIOLATION — must return 403."
                    )
                except urllib.error.HTTPError as e:
                    self.assertEqual(
                        403, e.code,
                        f"Expected 403 for personal table '{table}', got HTTP {e.code}."
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
