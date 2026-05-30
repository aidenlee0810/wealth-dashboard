"""
jobs/contracts/_base.py — Contract validation framework (stdlib only)

A lightweight, rigorous schema-validation layer for external API responses.
No pydantic dependency required, though the design mirrors pydantic's intent:
explicit field types, range constraints, cross-field validators, and versioning.

Key classes:
  Field    — declares a single field's type + constraints
  Contract — base class; subclass declares FIELDS + SCHEMA_VERSION + validators

Violation tracking:
  log_contract_violation() writes to cloud DB `contract_violations` table.
  If the table/DB doesn't exist yet, it degrades to a structured log line.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger("contracts")


class ContractError(Exception):
    """Raised on hard contract validation failure (schema drift)."""


_MISSING = object()


class Field:
    """Declares one field's type and constraints.

    Args:
        type_: expected Python type(s). Use a tuple for unions, e.g. (int, float).
        required: if True, missing/None is an error (unless allow_none).
        allow_none: if True, None passes even when required.
        gt/ge/lt/le: numeric bounds (exclusive/inclusive).
        min_len/max_len: for str/list.
        choices: allowed literal values.
        description: human-readable.
    """

    def __init__(
        self,
        type_: type | tuple[type, ...],
        *,
        required: bool = True,
        allow_none: bool = False,
        gt: Optional[float] = None,
        ge: Optional[float] = None,
        lt: Optional[float] = None,
        le: Optional[float] = None,
        min_len: Optional[int] = None,
        max_len: Optional[int] = None,
        choices: Optional[list] = None,
        description: str = "",
    ):
        self.type_ = type_
        self.required = required
        self.allow_none = allow_none
        self.gt = gt
        self.ge = ge
        self.lt = lt
        self.le = le
        self.min_len = min_len
        self.max_len = max_len
        self.choices = choices
        self.description = description

    def validate(self, name: str, value: Any) -> list[str]:
        errs: list[str] = []

        if value is _MISSING:
            if self.required and not self.allow_none:
                errs.append(f"{name}: required field missing")
            return errs

        if value is None:
            if not self.allow_none:
                errs.append(f"{name}: null not allowed")
            return errs

        # Type check (allow int where float expected)
        ok_type = isinstance(value, self.type_)
        if not ok_type and self.type_ in (float, (int, float)) and isinstance(value, int):
            ok_type = True
        if not ok_type:
            type_name = (
                self.type_.__name__ if isinstance(self.type_, type)
                else "/".join(t.__name__ for t in self.type_)
            )
            errs.append(f"{name}: expected {type_name}, got {type(value).__name__}")
            return errs  # don't run numeric checks on wrong type

        # Numeric bounds
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.gt is not None and not (value > self.gt):
                errs.append(f"{name}: {value} not > {self.gt}")
            if self.ge is not None and not (value >= self.ge):
                errs.append(f"{name}: {value} not >= {self.ge}")
            if self.lt is not None and not (value < self.lt):
                errs.append(f"{name}: {value} not < {self.lt}")
            if self.le is not None and not (value <= self.le):
                errs.append(f"{name}: {value} not <= {self.le}")

        # Length
        if hasattr(value, "__len__"):
            n = len(value)
            if self.min_len is not None and n < self.min_len:
                errs.append(f"{name}: length {n} < min {self.min_len}")
            if self.max_len is not None and n > self.max_len:
                errs.append(f"{name}: length {n} > max {self.max_len}")

        # Choices
        if self.choices is not None and value not in self.choices:
            errs.append(f"{name}: {value!r} not in allowed choices {self.choices}")

        return errs


class Contract:
    """Base class for an API response contract.

    Subclasses declare:
        SOURCE         = "finnhub"
        ENDPOINT       = "quote"
        SCHEMA_VERSION = "1.0.0"
        FIELDS         = {"c": Field(float, gt=0), ...}
        VALIDATORS     = [cross_field_fn, ...]   # optional

    Each validator fn takes the payload dict and returns a list[str] of errors.
    """

    SOURCE: str = "unknown"
    ENDPOINT: str = "unknown"
    SCHEMA_VERSION: str = "0.0.0"
    FIELDS: dict[str, Field] = {}
    VALIDATORS: list[Callable[[dict], list[str]]] = []

    @classmethod
    def validate(cls, payload: Any) -> tuple[bool, list[str], dict]:
        """Validate a raw payload.

        Returns (ok, errors, cleaned_dict).
        cleaned_dict contains only declared fields (extras dropped).
        """
        errors: list[str] = []

        if not isinstance(payload, dict):
            return False, [f"payload is {type(payload).__name__}, expected dict"], {}

        cleaned: dict[str, Any] = {}
        for name, field in cls.FIELDS.items():
            value = payload.get(name, _MISSING)
            field_errs = field.validate(name, value)
            errors.extend(field_errs)
            if value is not _MISSING and not field_errs:
                cleaned[name] = value

        # Cross-field validators (only if field-level passed)
        if not errors:
            for validator in cls.VALIDATORS:
                try:
                    errors.extend(validator(payload) or [])
                except Exception as e:
                    errors.append(f"validator {getattr(validator, '__name__', '?')} raised: {e}")

        return (len(errors) == 0), errors, cleaned

    @classmethod
    def validate_or_raise(cls, payload: Any, ticker: str = "") -> dict:
        """Validate; on failure, log the violation and raise ContractError."""
        ok, errors, cleaned = cls.validate(payload)
        if not ok:
            log_contract_violation(
                source=cls.SOURCE,
                endpoint=cls.ENDPOINT,
                ticker=ticker,
                schema_version=cls.SCHEMA_VERSION,
                errors=errors,
                payload=payload,
            )
            raise ContractError(
                f"{cls.SOURCE}/{cls.ENDPOINT} schema violation"
                + (f" for {ticker}" if ticker else "")
                + f": {errors[:5]}"
            )
        return cleaned

    @classmethod
    def log_violation(cls, ticker: str, errors: list[str], payload: Any) -> None:
        log_contract_violation(
            source=cls.SOURCE,
            endpoint=cls.ENDPOINT,
            ticker=ticker,
            schema_version=cls.SCHEMA_VERSION,
            errors=errors,
            payload=payload,
        )


def _payload_hash(payload: Any) -> str:
    try:
        s = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        s = str(payload)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def log_contract_violation(
    *,
    source: str,
    endpoint: str,
    ticker: str,
    schema_version: str,
    errors: list[str],
    payload: Any,
    severity: str = "high",   # must be one of: low/medium/high/critical
) -> None:
    """Persist a contract violation to the cloud DB; degrade to log if unavailable.

    Writes to contract_violations table (created in migration 001).
    Never raises — violation logging must not break the snapshot pipeline.
    """
    error_message = "; ".join(errors[:10])
    payload_hash = _payload_hash(payload)
    detected_at = datetime.now(timezone.utc).isoformat()

    # Structured log line (always)
    log.warning(
        "contract_violation source=%s endpoint=%s ticker=%s schema=%s errors=%s",
        source, endpoint, ticker, schema_version, error_message,
    )

    # Try DB persistence (best-effort)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        import db
        with db.cloud() as conn:
            conn.execute(
                """
                INSERT INTO contract_violations
                    (detected_at, source, endpoint, ticker,
                     expected_schema_version, actual_payload_hash,
                     error_message, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (detected_at, source, endpoint, ticker or None,
                 schema_version, payload_hash, error_message, severity),
            )
    except Exception as e:
        log.debug("contract_violation DB persist skipped: %s", e)
