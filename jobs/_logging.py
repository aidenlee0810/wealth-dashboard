"""
jobs/_logging.py — Structured JSON logging foundation (Plan §32)

Every job script should `from jobs._logging import get_logger`
and use the bound logger for all log output. This:
  1. Produces machine-readable JSON lines for grep/jq/post-mortem
  2. Auto-binds snapshot_run_id, market_date, executed_at_utc
  3. Writes to logs/snapshot_YYYY-MM-DD.jsonl (gzip after 7 days)
  4. Streams human-readable to stderr for live tail

Usage:
    from jobs._logging import get_logger
    log = get_logger("daily_snapshot", run_id="abc123", market_date="2026-05-28")
    log.info("stage1_started", ticker_count=800)
    log.warning("api_rate_limit", provider="finnhub", retry_after=60)
    log.error("data_quality_critical", ticker="NVDA", dq=42)
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc_now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class StructuredLogger:
    """JSON-line logger with bound context.

    Each log line is one JSON object with keys:
      ts, level, msg, run_id, job, market_date, ...kwargs
    """

    def __init__(self, job: str, bindings: dict[str, Any] | None = None,
                 log_file: Path | None = None):
        self.job = job
        self.bindings = dict(bindings or {})
        self.bindings.setdefault("run_id", str(uuid.uuid4())[:8])
        self.bindings.setdefault("executed_at_utc", _utc_now_iso())
        self.log_file = log_file or (LOG_DIR / f"snapshot_{_utc_now_date()}.jsonl")
        self._file_handle = None

    def _open(self):
        if self._file_handle is None or self._file_handle.closed:
            self._file_handle = open(self.log_file, "a", encoding="utf-8")
        return self._file_handle

    def _write(self, level: str, msg: str, **kwargs: Any) -> None:
        entry = {
            "ts": _utc_now_iso(),
            "level": level,
            "job": self.job,
            "msg": msg,
            **self.bindings,
            **kwargs,
        }
        line = json.dumps(entry, default=str, ensure_ascii=False)

        # Write to JSON file
        try:
            f = self._open()
            f.write(line + "\n")
            f.flush()
        except Exception as e:
            sys.stderr.write(f"[logging error: {e}]\n")

        # Stream human-readable to stderr
        ctx_str = ""
        ctx_keys = [k for k in kwargs if k not in ("ts", "level", "job", "msg")]
        if ctx_keys:
            ctx_str = " " + " ".join(f"{k}={kwargs[k]}" for k in ctx_keys)
        marker = {"DEBUG": "  ", "INFO": "  ", "WARNING": "⚠️ ",
                  "ERROR": "❌ ", "CRITICAL": "🚨 "}.get(level, "  ")
        sys.stderr.write(f"{marker}[{level}] {self.job}: {msg}{ctx_str}\n")
        sys.stderr.flush()

    def debug(self, msg: str, **kwargs):    self._write("DEBUG", msg, **kwargs)
    def info(self, msg: str, **kwargs):     self._write("INFO", msg, **kwargs)
    def warning(self, msg: str, **kwargs):  self._write("WARNING", msg, **kwargs)
    def error(self, msg: str, **kwargs):    self._write("ERROR", msg, **kwargs)
    def critical(self, msg: str, **kwargs): self._write("CRITICAL", msg, **kwargs)

    def bind(self, **new_bindings: Any) -> "StructuredLogger":
        """Return a new logger with additional context bindings."""
        merged = {**self.bindings, **new_bindings}
        return StructuredLogger(self.job, merged, self.log_file)

    def close(self):
        if self._file_handle and not self._file_handle.closed:
            self._file_handle.close()


def get_logger(job: str, **bindings: Any) -> StructuredLogger:
    """Factory for a structured logger bound to job + initial context."""
    return StructuredLogger(job, bindings)


# ---------------------------------------------------------------------------
# Convenience: timing context manager
# ---------------------------------------------------------------------------
class timed:
    """Context manager that logs duration of a code block.

    Usage:
        with timed(log, "fetch_prices", ticker_count=800):
            ... do work ...
    """

    def __init__(self, log: StructuredLogger, op: str, **context: Any):
        self.log = log
        self.op = op
        self.context = context
        self.start = None

    def __enter__(self):
        self.start = time.monotonic()
        self.log.info(f"{self.op}_started", **self.context)
        return self

    def __exit__(self, exc_type, exc_val, tb):
        elapsed = time.monotonic() - self.start
        if exc_type is None:
            self.log.info(
                f"{self.op}_completed",
                elapsed_seconds=round(elapsed, 3),
                **self.context,
            )
        else:
            self.log.error(
                f"{self.op}_failed",
                elapsed_seconds=round(elapsed, 3),
                error_type=exc_type.__name__,
                error_msg=str(exc_val),
                **self.context,
            )
        return False  # don't suppress exception


# ---------------------------------------------------------------------------
# Simple log retention (keep 30 days)
# ---------------------------------------------------------------------------
def prune_old_logs(keep_days: int = 30) -> int:
    """Delete log files older than keep_days. Returns count deleted."""
    import datetime as dt
    cutoff = dt.datetime.now(timezone.utc) - dt.timedelta(days=keep_days)
    deleted = 0
    for f in LOG_DIR.glob("snapshot_*.jsonl*"):
        try:
            mtime = dt.datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted


if __name__ == "__main__":
    # Self-test
    log = get_logger("logging_test", run_id="test-001", market_date="2026-05-28")
    log.info("test_start")
    log.warning("test_warn", reason="just a drill")
    with timed(log, "demo_op", n_items=42):
        time.sleep(0.05)
    log.info("test_end")
    print(f"\nLog file: {log.log_file}")
