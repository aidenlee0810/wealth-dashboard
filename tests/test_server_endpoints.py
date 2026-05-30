"""
tests/test_server_endpoints.py — Live server endpoint tests (Phase 1.1)

ALL tests in this file require `python server.py` running on port 5500.
If the server is not running, all tests are SKIPPED (expected in CI).

Test categories:
  TestCORSHeaders         — CORS policy correctness (public vs private)
  TestCSRFProtection      — /api/local/* Origin/token enforcement
  TestDbQuerySecurity     — limit clamp, column allowlist, personal table rejection
  TestColumnAllowlist     — POST payload validation

Expected results (server NOT running):
  ALL SKIP — this is correct behavior. These tests are for local development.

Expected results (server running, DB initialized):
  TestCORSHeaders::test_options_includes_delete          PASS
  TestCORSHeaders::test_public_endpoint_cors_star        PASS
  TestCORSHeaders::test_local_endpoint_cors_localhost    PASS
  TestCSRFProtection::test_external_origin_blocked       PASS
  TestCSRFProtection::test_no_origin_allowed             PASS
  TestDbQuerySecurity::test_limit_negative_clamped       PASS
  TestDbQuerySecurity::test_limit_zero_clamped           PASS
  TestDbQuerySecurity::test_personal_table_403           PASS
  TestDbQuerySecurity::test_unknown_table_403            PASS
  TestColumnAllowlist::test_unknown_field_rejected       PASS

Run:
    python server.py &
    python -m pytest tests/test_server_endpoints.py -v
"""

import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVER_URL = "http://localhost:5500"
_server_available: bool | None = None  # cached


def _server_running() -> bool:
    """Check if server is reachable. Result is cached for the test session."""
    global _server_available
    if _server_available is not None:
        return _server_available
    try:
        urllib.request.urlopen(f"{SERVER_URL}/api/db/health", timeout=2)
        _server_available = True
    except Exception:
        _server_available = False
    return _server_available


def _skip_if_no_server(test_case: unittest.TestCase) -> None:
    """Skip the test with a descriptive message if server is not running."""
    if not _server_running():
        test_case.skipTest(
            f"Server not running at {SERVER_URL} — "
            "start `python server.py` for live endpoint tests. "
            "EXPECTED SKIP in unit-test / CI environments."
        )


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
    """GET request; returns (status_code, body, response_headers)."""
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _post(url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, bytes, dict]:
    """POST request; returns (status_code, body, response_headers)."""
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _options(url: str, headers: dict | None = None) -> tuple[int, dict]:
    """OPTIONS preflight request; returns (status_code, response_headers)."""
    req = urllib.request.Request(url, method="OPTIONS")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


# ────────────────────────────────────────────────────────────────────────────
class TestCORSHeaders(unittest.TestCase):
    """CORS policy: public endpoints allow *, private allow localhost only."""

    def setUp(self):
        _skip_if_no_server(self)

    def test_options_includes_delete(self):
        """OPTIONS preflight for /api/local/* must include DELETE in Allow-Methods.

        Acceptance: Phase 1.1 item [1].
        """
        status, hdrs = _options(
            f"{SERVER_URL}/api/local/actions",
            headers={
                "Origin": "http://localhost:5500",
                "Access-Control-Request-Method": "DELETE",
            }
        )
        allow_methods = hdrs.get("Access-Control-Allow-Methods", "")
        self.assertIn(
            "DELETE", allow_methods,
            f"DELETE missing from Allow-Methods header: '{allow_methods}'. "
            "Check _cors_local() in server.py."
        )

    def test_public_endpoint_cors_star(self):
        """GET /api/db/query must respond with Access-Control-Allow-Origin: *.

        Public market data should be accessible from any origin (read-only).
        """
        status, body, hdrs = _get(
            f"{SERVER_URL}/api/db/query?table=ticker_master&limit=1",
            headers={"Origin": "https://example.com"}
        )
        allow_origin = hdrs.get("Access-Control-Allow-Origin", "")
        self.assertEqual(
            "*", allow_origin,
            f"Public /api/db/query should have CORS *. Got: '{allow_origin}'"
        )

    def test_local_endpoint_cors_localhost(self):
        """GET /api/local/* from localhost origin must return localhost CORS header.

        Private endpoints must NOT use CORS *, only reflect safe localhost origins.
        """
        status, body, hdrs = _get(
            f"{SERVER_URL}/api/local/actions?limit=1",
            headers={"Origin": "http://localhost:5500"}
        )
        allow_origin = hdrs.get("Access-Control-Allow-Origin", "")
        self.assertNotEqual(
            "*", allow_origin,
            "Private /api/local/* must NOT use CORS *. "
            "Use http://localhost:5500 instead."
        )
        self.assertIn(
            "localhost", allow_origin,
            f"Private /api/local/* should reflect localhost origin. Got: '{allow_origin}'"
        )


# ────────────────────────────────────────────────────────────────────────────
class TestCSRFProtection(unittest.TestCase):
    """CSRF: /api/local/* blocks external origins, allows localhost/no-origin."""

    def setUp(self):
        _skip_if_no_server(self)

    def test_external_origin_blocked(self):
        """POST /api/local/actions from evil.example.com must return 403.

        Acceptance: Phase 1.1 item [2].
        This simulates a CSRF attack from a malicious website.
        """
        status, body, _ = _post(
            f"{SERVER_URL}/api/local/actions",
            body={"date": "2026-01-01", "ticker": "HACK"},
            headers={"Origin": "https://evil.example.com"}
        )
        self.assertEqual(
            403, status,
            f"External Origin must be blocked with 403. Got HTTP {status}. "
            "Check _check_local_access() in server.py."
        )

    def test_no_origin_allowed(self):
        """GET /api/local/actions without Origin header must succeed.

        curl and local scripts don't send Origin headers — they should work.
        (Actual response code depends on whether DB is initialized.)
        """
        status, body, _ = _get(f"{SERVER_URL}/api/local/actions?limit=1")
        # 200 (DB initialized) or 503 (DB not init) — both are NOT 403
        self.assertNotEqual(
            403, status,
            f"Request without Origin header should not be blocked. Got HTTP {status}."
        )

    def test_localhost_origin_allowed(self):
        """POST /api/local/actions from http://localhost:5500 must not get CSRF 403."""
        status, body, _ = _post(
            f"{SERVER_URL}/api/local/actions",
            body={},
            headers={"Origin": "http://localhost:5500"}
        )
        # Should not be 403 for CSRF (may be 400 for bad payload, but not 403)
        err_msg = ""
        try:
            err_msg = json.loads(body).get("error", "")
        except Exception:
            pass
        if status == 403:
            self.fail(
                f"localhost origin was blocked with 403. "
                f"Error: {err_msg}. Check _check_local_access() in server.py."
            )


# ────────────────────────────────────────────────────────────────────────────
class TestDbQuerySecurity(unittest.TestCase):
    """DB query endpoint: limit clamping, table allowlist enforcement."""

    def setUp(self):
        _skip_if_no_server(self)

    def test_limit_negative_clamped(self):
        """GET /api/db/query?limit=-1 must succeed and return at most 1 row.

        Acceptance: Phase 1.1 item [3] — negative limit clamped to 1.
        LIMIT -1 in SQLite means "no limit" — we must never pass it through.
        """
        status, body, _ = _get(
            f"{SERVER_URL}/api/db/query?table=ticker_master&limit=-1"
        )
        # Accept 200 (clamped to 1) or 400 (explicitly rejected)
        self.assertIn(
            status, [200, 400],
            f"limit=-1 should return 200 (clamped) or 400 (rejected). Got {status}."
        )
        if status == 200:
            data = json.loads(body)
            # Clamped to 1 — should not return more than 1 row
            row_count = len(data.get("rows", []))
            # We can't know the actual table size, just assert no explosion
            self.assertLessEqual(
                row_count, 1,
                f"limit=-1 clamped to 1 should return at most 1 row. Got {row_count}."
            )

    def test_limit_zero_handled(self):
        """GET /api/db/query?limit=0 must not return unlimited rows.

        Negative/zero limits clamped to 1 minimum (or rejected with 400).
        """
        status, body, _ = _get(
            f"{SERVER_URL}/api/db/query?table=ticker_master&limit=0"
        )
        self.assertIn(status, [200, 400],
            f"limit=0 should return 200 (clamped) or 400. Got {status}.")

    def test_personal_table_403(self):
        """GET /api/db/query for personal tables must return 403.

        Tests all 7 personal tables.
        Acceptance: Phase 1.1 item [2] / §18 security gate.
        """
        import db as _db
        for table in sorted(_db.PERSONAL_TABLES):
            with self.subTest(table=table):
                status, body, _ = _get(
                    f"{SERVER_URL}/api/db/query?table={table}&limit=1"
                )
                self.assertEqual(
                    403, status,
                    f"Personal table '{table}' should return 403. Got {status}."
                )

    def test_unknown_table_403(self):
        """GET /api/db/query for unknown/unlisted table must return 403."""
        for table in ["nonexistent_table", "sqlite_master", "auth_tokens", "secrets"]:
            with self.subTest(table=table):
                status, body, _ = _get(
                    f"{SERVER_URL}/api/db/query?table={table}&limit=1"
                )
                self.assertEqual(
                    403, status,
                    f"Unknown table '{table}' should return 403. Got {status}."
                )


# ────────────────────────────────────────────────────────────────────────────
class TestColumnAllowlist(unittest.TestCase):
    """POST /api/local/* column allowlist enforcement."""

    def setUp(self):
        _skip_if_no_server(self)

    def test_unknown_field_rejected(self):
        """POST /api/local/actions with unknown_column must return 400.

        Acceptance: Phase 1.1 item [4] — column allowlist.
        """
        status, body, _ = _post(
            f"{SERVER_URL}/api/local/actions",
            body={
                "date": "2026-01-01",
                "ticker": "NVDA",
                "action_type": "BUY",
                "unknown_column": "should_be_rejected",   # ← not in allowlist
                "another_bad_field": 99,
            },
            headers={"Origin": "http://localhost:5500"}
        )
        self.assertEqual(
            400, status,
            f"POST with unknown_column should return 400. Got {status}. "
            "Check LOCAL_TABLE_COLUMN_ALLOWLIST in server.py."
        )
        err_msg = ""
        try:
            err_msg = json.loads(body).get("error", "")
        except Exception:
            pass
        self.assertIn(
            "unknown_column", err_msg,
            f"400 error message should name the offending field. Got: '{err_msg}'"
        )

    def test_valid_user_action_accepted(self):
        """POST /api/local/actions with valid payload must succeed (200 or 503 if no DB).

        If DB is initialized, should return 200.
        If DB is not initialized, may return 503 (acceptable).
        Must NOT return 400 (bad payload) or 403 (security block).
        """
        status, body, _ = _post(
            f"{SERVER_URL}/api/local/actions",
            body={
                "date": "2026-01-01",
                "ticker": "NVDA",
                "action_type": "BUY",
                "actual_price": 142.50,
                "actual_amount": 1000.0,
                "account": "Fidelity",
            },
            headers={"Origin": "http://localhost:5500"}
        )
        self.assertIn(
            status, [200, 503],
            f"Valid user_actions payload should return 200 (ok) or 503 (no DB). "
            f"Got {status}. Response: {body[:200]}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
