"""
jobs/model_registry.py — Experiment registry (Plan §31, Phase 10-B).

Tracks every scoring model through its lifecycle:
  experimental → shadow → production → deprecated

Public API
----------
  register(conn, model_id, family, version, config, description, commit_sha)
      → Insert or IGNORE (idempotent).  Returns the model_id.

  set_status(conn, model_id, status)
      → Advance status; sets promoted_at / retired_at timestamps.

  update_shadow_stats(conn, model_id, stats_dict)
      → Write shadow-period evaluation metrics.

  get_production(conn, family)
      → Dict for the current 'production' model of a family.

  list_models(conn, family=None, status=None)
      → List of dicts, newest first.

  promote(conn, model_id)
      → Move model_id to 'production'; demote current production to 'deprecated'.

  retire(conn, model_id)
      → Mark model as 'deprecated'.

Idempotent: register() uses INSERT OR IGNORE; set_status/promote/retire are safe
to re-run.
"""

from __future__ import annotations

import datetime
import json
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def register(
    conn,
    model_id: str,
    family: str,
    version: str,
    config: Optional[dict] = None,
    description: str = "",
    commit_sha: Optional[str] = None,
    status: str = "experimental",
) -> str:
    """Register a model.  INSERT OR IGNORE — safe to call every snapshot run."""
    conn.execute(
        """INSERT OR IGNORE INTO model_registry
           (model_id, family, version, status, created_at,
            code_commit_sha, config_json, description)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            model_id,
            family,
            version,
            status,
            _now(),
            commit_sha,
            json.dumps(config) if config else None,
            description,
        ),
    )
    return model_id


def set_status(conn, model_id: str, status: str) -> None:
    """Directly set status; manages promoted_at / retired_at timestamps."""
    valid = {"experimental", "shadow", "production", "deprecated"}
    if status not in valid:
        raise ValueError(f"Invalid status '{status}'. Must be one of {valid}")

    now = _now()
    if status == "production":
        conn.execute(
            "UPDATE model_registry SET status=?, promoted_at=? WHERE model_id=?",
            (status, now, model_id),
        )
    elif status == "deprecated":
        conn.execute(
            "UPDATE model_registry SET status=?, retired_at=? WHERE model_id=?",
            (status, now, model_id),
        )
    else:
        conn.execute(
            "UPDATE model_registry SET status=? WHERE model_id=?",
            (status, model_id),
        )


def update_shadow_stats(conn, model_id: str, stats: dict) -> None:
    """Write shadow evaluation metrics after the shadow period ends."""
    conn.execute(
        """UPDATE model_registry SET
             shadow_period_start   = COALESCE(shadow_period_start, :start),
             shadow_period_end     = :end,
             shadow_signal_count   = :count,
             shadow_hit_rate       = :hit_rate,
             shadow_ev             = :ev,
             shadow_vs_production_correlation = :corr,
             graduation_decision   = :decision
           WHERE model_id = :model_id""",
        {
            "start":     stats.get("period_start"),
            "end":       stats.get("period_end"),
            "count":     stats.get("signal_count"),
            "hit_rate":  stats.get("hit_rate"),
            "ev":        stats.get("ev"),
            "corr":      stats.get("vs_production_correlation"),
            "decision":  stats.get("graduation_decision", "pending"),
            "model_id":  model_id,
        },
    )


def promote(conn, family: str, new_model_id: str) -> None:
    """Promote *new_model_id* to production; retire the current production model.

    The operation is atomic within the caller's transaction.
    """
    # Find current production model for this family
    current = conn.execute(
        "SELECT model_id FROM model_registry WHERE family=? AND status='production'",
        (family,),
    ).fetchone()

    now = _now()

    if current and current["model_id"] != new_model_id:
        # Retire the old one
        conn.execute(
            "UPDATE model_registry SET status='deprecated', retired_at=? WHERE model_id=?",
            (now, current["model_id"]),
        )

    # Promote the new one
    conn.execute(
        "UPDATE model_registry SET status='production', promoted_at=? WHERE model_id=?",
        (now, new_model_id),
    )


def retire(conn, model_id: str) -> None:
    """Mark *model_id* as deprecated."""
    set_status(conn, model_id, "deprecated")


def get_production(conn, family: str) -> Optional[dict]:
    """Return the current production model dict for *family*, or None."""
    row = conn.execute(
        "SELECT * FROM model_registry WHERE family=? AND status='production' "
        "ORDER BY promoted_at DESC LIMIT 1",
        (family,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("config_json"):
        try:
            d["config"] = json.loads(d["config_json"])
        except (ValueError, TypeError):
            d["config"] = None
    return d


def list_models(
    conn,
    family: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """List models, newest created_at first.  Filter by family and/or status."""
    clauses = []
    params: list = []
    if family:
        clauses.append("family = ?")
        params.append(family)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM model_registry {where} ORDER BY created_at DESC",
        params,
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("config_json"):
            try:
                d["config"] = json.loads(d["config_json"])
            except (ValueError, TypeError):
                d["config"] = None
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Views helper: build registry summary for latest_model_registry.json
# ---------------------------------------------------------------------------

def build_registry_view(conn) -> dict:
    """Return a dict suitable for serialization into latest_model_registry.json."""
    all_models = list_models(conn)
    production = [m for m in all_models if m["status"] == "production"]
    shadow     = [m for m in all_models if m["status"] == "shadow"]
    exp        = [m for m in all_models if m["status"] == "experimental"]
    deprecated = [m for m in all_models if m["status"] == "deprecated"]

    def _strip(m):
        """Drop config_json blob from view output (keep parsed config)."""
        m2 = {k: v for k, v in m.items() if k != "config_json"}
        return m2

    return {
        "production":   [_strip(m) for m in production],
        "shadow":       [_strip(m) for m in shadow],
        "experimental": [_strip(m) for m in exp],
        "deprecated":   [_strip(m) for m in deprecated],
        "total":        len(all_models),
    }
