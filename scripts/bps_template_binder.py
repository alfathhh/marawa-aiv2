#!/usr/bin/env python3
"""Stateless, deterministic SQL-template binder for bps_registry templates.

Contract-level verifier (not yet wired to any runtime): validates parameters
against the declared schema and binds values through psycopg placeholder
parameters only — never string interpolation. No network, no LLM.

Audit 2026-08-15 fixes:
  H1  Row-limit ownership is read from the template's declared `has_own_limit`
      field instead of sniffing the substring "LIMIT" out of the SQL. The old
      check was case-sensitive and matched any occurrence of the word, so
      `publication_list` (which carries its own LIMIT) silently lost the
      server-side cap while its caller-supplied `page_size` went unbounded.
  H2  New `like` type escapes %, _ and \\ so a caller searching for "%" cannot
      match the entire catalogue.
  --  Integer parameters must declare a `max:` bound; text parameters are length
      capped. Unbounded integers were reachable from caller input.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

INT_TYPES = (int,)
NUM_TYPES = (int, float, Decimal)
STR_TYPES = (str,)
JSON_TYPES = (dict, list)
BOOL_TYPES = (bool,)

# Hard ceiling applied to every row limit regardless of what a template declares.
MAX_ROW_LIMIT = 1000
# Hard ceiling on any bound text value; protects the planner and the log volume.
MAX_TEXT_LENGTH = 512
# Hard ceiling on jsonb nesting. A deeply nested payload is a cheap way to burn
# CPU in the JSON parser before the query even runs.
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 1000


class TemplateValidationError(ValueError):
    pass


PARSER_RULES: dict[str, tuple[type, ...]] = {
    "text": STR_TYPES,
    "like": STR_TYPES,
    "integer": INT_TYPES,
    "numeric": NUM_TYPES,
    "jsonb": JSON_TYPES,
    "boolean": BOOL_TYPES,
}


def _schema_entry(raw: str) -> tuple[str, bool, int | None]:
    """Parse a declaration.

    'text'            -> ('text', False, None)
    'text|nullable'   -> ('text', True, None)
    'integer|max:100' -> ('integer', False, 100)
    """
    parts = [part.strip() for part in raw.split("|")]
    base = parts[0]
    nullable = "nullable" in parts[1:]
    maximum: int | None = None
    for part in parts[1:]:
        if part.startswith("max:"):
            try:
                maximum = int(part[4:])
            except ValueError as exc:
                raise TemplateValidationError(f"invalid max bound in {raw!r}") from exc
    return base, nullable, maximum


def escape_like(value: str) -> str:
    r"""Escape LIKE/ILIKE metacharacters (audit H2).

    Must be paired with ESCAPE '\' in the SQL template.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _check_value(name: str, value: Any, base: str, maximum: int | None) -> Any:
    types = PARSER_RULES[base]
    if isinstance(value, bool) and base != "boolean":
        raise TemplateValidationError(
            f"parameter {name!r}: expected {base}, got bool (bool must use type 'boolean')"
        )
    if not isinstance(value, types):
        raise TemplateValidationError(f"parameter {name!r}: type mismatch, expected {base}")

    if base in ("text", "like"):
        if len(value) > MAX_TEXT_LENGTH:
            raise TemplateValidationError(
                f"parameter {name!r}: exceeds {MAX_TEXT_LENGTH} characters"
            )
        if "\x00" in value:
            raise TemplateValidationError(f"parameter {name!r}: NUL byte not allowed")
        return escape_like(value) if base == "like" else value

    if base in ("integer", "numeric"):
        # AUDIT R: NaN defeats every comparison — `nan < 0` is False and
        # `nan > maximum` is False — so both bounds checks "pass" and it reaches
        # the database. Infinity passes the lower bound the same way.
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise TemplateValidationError(f"parameter {name!r}: not a finite number")
        if isinstance(value, Decimal) and not value.is_finite():
            raise TemplateValidationError(f"parameter {name!r}: not a finite number")
        if value < 0:
            raise TemplateValidationError(f"parameter {name!r}: must not be negative")
        if maximum is not None and value > maximum:
            raise TemplateValidationError(
                f"parameter {name!r}: {value} exceeds declared maximum {maximum}"
            )
        if maximum is None and base == "integer":
            raise TemplateValidationError(
                f"parameter {name!r}: integer parameters must declare a 'max:' bound"
            )

    if base == "jsonb":
        _check_json_shape(name, value)
    return value


def _check_json_shape(name: str, value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    """Bound the nesting depth and node count of a jsonb parameter (audit S)."""
    if budget is None:
        budget = [MAX_JSON_NODES]
    if depth > MAX_JSON_DEPTH:
        raise TemplateValidationError(f"parameter {name!r}: nesting deeper than {MAX_JSON_DEPTH}")
    budget[0] -= 1
    if budget[0] < 0:
        raise TemplateValidationError(f"parameter {name!r}: more than {MAX_JSON_NODES} nodes")
    if isinstance(value, dict):
        for item in value.values():
            _check_json_shape(name, item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _check_json_shape(name, item, depth + 1, budget)


def bind_template(template: dict[str, Any], params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate params against template['parameter_schema'] and return (sql, bound).

    Required: params without "|nullable" must be present. Unknown keys rejected.
    Row limit is enforced here (server-side, never from caller params).
    """
    template_id = template.get("template_id", "<unknown>")
    schema = template["parameter_schema"]
    sql = template["sql_template"]

    if "has_own_limit" not in template:
        raise TemplateValidationError(
            f"template {template_id} must declare has_own_limit; limit ownership is "
            "never inferred from SQL text"
        )
    has_own_limit = bool(template["has_own_limit"])

    row_limit = template.get("row_limit", 100)
    # AUDIT Q: a None row_limit used to slip past both checks and end up bound
    # as `LIMIT NULL`, which in PostgreSQL means NO LIMIT — the exact opposite
    # of the intent, and silent: the query succeeds and simply returns
    # everything. There is no valid reason for a template to omit its limit.
    if not has_own_limit:
        if not isinstance(row_limit, int) or isinstance(row_limit, bool):
            raise TemplateValidationError(
                f"template {template_id} row_limit must be an integer, got {row_limit!r}"
            )
        if not 0 < row_limit <= MAX_ROW_LIMIT:
            raise TemplateValidationError(
                f"template {template_id} row_limit {row_limit} outside 1..{MAX_ROW_LIMIT}"
            )

    declared: dict[str, tuple[str, bool, int | None]] = {}
    for name, raw in schema.items():
        base, nullable, maximum = _schema_entry(raw)
        if base not in PARSER_RULES:
            raise TemplateValidationError(
                f"template schema has unknown type {raw!r} for {name!r}"
            )
        declared[name] = (base, nullable, maximum)

    for name in params:
        if name not in declared:
            raise TemplateValidationError(
                f"unknown parameter {name!r} for template {template_id}"
            )

    bound: dict[str, Any] = {}
    for name, (base, nullable, maximum) in declared.items():
        if name not in params:
            if nullable:
                bound[name] = None
                continue
            raise TemplateValidationError(
                f"required parameter {name!r} missing for template {template_id}"
            )
        value = params[name]
        if value is None:
            if not nullable:
                raise TemplateValidationError(f"required parameter {name!r} cannot be null")
            bound[name] = None
            continue
        bound[name] = _check_value(name, value, base, maximum)

    if "%(row_limit)s" in sql:
        raise TemplateValidationError("row_limit must not be caller-bindable")

    if has_own_limit:
        # Template paginates itself; its own bounds were validated above via the
        # declared max: on page_size/offset. Do not append a second LIMIT.
        return sql, bound

    # Wrapping instead of appending keeps the limit correct for SQL that ends in
    # a comment, a UNION, or anything else where a trailing clause would bind to
    # the wrong query.
    final_sql = (
        f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) AS bound_result LIMIT %(row_limit)s"
    )
    return final_sql, {**bound, "row_limit": row_limit}
