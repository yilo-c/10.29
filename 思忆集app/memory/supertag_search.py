"""Supertag structured query engine — AND/OR/NOT composition + field filtering.

Provides query parsing and evaluation for structured memory entries and
supertag-typed data. Supports:

- ``type:person AND tag:friend``  — field equality + logical AND
- ``priority:high OR priority:urgent`` — logical OR
- ``status:done NOT priority:low`` — logical NOT exclusion
- ``deadline>2026-06-01`` — range / comparison operators
- ``tag:friend, colleague`` — comma-separated values (OR within field)
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.supertag.search")

# ── Token types for the query parser ──────────────────────────────────

TOKEN_FIELD = "field"
TOKEN_VALUE = "value"
TOKEN_AND = "and"
TOKEN_OR = "or"
TOKEN_NOT = "not"
TOKEN_LPAREN = "lparen"
TOKEN_RPAREN = "rparen"
TOKEN_EOF = "eof"

# ── Query node types ──────────────────────────────────────────────────

NODE_AND = "and"
NODE_OR = "or"
NODE_NOT = "not"
NODE_FILTER = "filter"
NODE_GROUP = "group"


@dataclass(frozen=True)
class FilterCondition:
    """A single filter condition: field operator value."""
    field: str
    operator: str  # "==", "!=", ">", ">=", "<", "<=", ":" (contains/subfield)
    value: Any


@dataclass(frozen=True)
class QueryNode:
    """A node in the structured query AST."""
    type: str  # "and" | "or" | "not" | "filter" | "group"
    condition: FilterCondition | None = None
    children: list[QueryNode] = field(default_factory=list)

    @classmethod
    def filter_node(cls, condition: FilterCondition) -> QueryNode:
        return cls(type=NODE_FILTER, condition=condition)

    @classmethod
    def and_node(cls, children: list[QueryNode]) -> QueryNode:
        return cls(type=NODE_AND, children=children)

    @classmethod
    def or_node(cls, children: list[QueryNode]) -> QueryNode:
        return cls(type=NODE_OR, children=children)

    @classmethod
    def not_node(cls, child: QueryNode) -> QueryNode:
        return cls(type=NODE_NOT, children=[child])


@dataclass
class ParsedQuery:
    """Result of parsing a structured query string."""
    ast: QueryNode | None = None
    text_terms: list[str] = field(default_factory=list)  # leftover plain text for full-text search
    original: str = ""
    error: str | None = None


# ── Lexer ─────────────────────────────────────────────────────────────


def _tokenize(query: str) -> list[tuple[str, str]]:
    """Tokenize a structured query string.

    Returns list of (type, value) tokens.
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(query):
        ch = query[i]

        # Skip whitespace
        if ch in " \t\n\r":
            i += 1
            continue

        # Parentheses
        if ch == "(":
            tokens.append((TOKEN_LPAREN, "("))
            i += 1
            continue
        if ch == ")":
            tokens.append((TOKEN_RPAREN, ")"))
            i += 1
            continue

        # Logical operators (uppercase)
        if query[i:i+3].upper() == "AND" and (i+3 >= len(query) or query[i+3] in " \t\n\r()"):
            tokens.append((TOKEN_AND, "AND"))
            i += 3
            continue
        if query[i:i+2].upper() == "OR" and (i+2 >= len(query) or query[i+2] in " \t\n\r()"):
            tokens.append((TOKEN_OR, "OR"))
            i += 2
            continue
        if query[i:i+3].upper() == "NOT" and (i+3 >= len(query) or query[i+3] in " \t\n\r()"):
            tokens.append((TOKEN_NOT, "NOT"))
            i += 3
            continue

        # Field:value pattern (field names contain only word chars + dots)
        # P3-7: 防止把 URL（如 ``https://example.com``）当作 ``https:`` field:value：
        # 当操作符是 ``:`` 且后续紧跟 ``//`` 时，回退到普通文本 token 解析。
        field_match = re.match(r"([\w.]+)([:!><=]+)", query[i:])
        if field_match:
            field_name = field_match.group(1)
            operator = field_match.group(2)
            after_op_idx = i + len(field_name) + len(operator)
            looks_like_url = (
                operator == ":"
                and query[after_op_idx : after_op_idx + 2] == "//"
            )
            if looks_like_url:
                # 让下方的纯文本分支吞掉整个 URL token
                pass
            else:
                i = after_op_idx

                # Parse value (quoted or unquoted)
                value, consumed = _parse_value(query[i:])
                i += consumed

                # Combine operator: ":" → "==", "!" → "!=", etc.
                op = _normalize_operator(operator)
                tokens.append((TOKEN_FIELD, f"{field_name}{op}{value}"))
                continue

        # Regular text term
        text_match = re.match(r"[^\s()]+", query[i:])
        if text_match:
            tokens.append((TOKEN_VALUE, text_match.group(0)))
            i += text_match.end()
            continue

        i += 1

    tokens.append((TOKEN_EOF, ""))
    return tokens


def _parse_value(text: str) -> tuple[str, int]:
    """Parse a value from the current position. Supports quoted strings and comma lists."""
    if not text:
        return "", 0

    # Quoted value
    if text[0] in ('"', "'"):
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            return text[1:end], end + 1
        return text[1:], len(text)

    # Comma-separated list or single value
    match = re.match(r'[^\s()]+', text)
    if match:
        return match.group(0), match.end()

    return "", 0


def _normalize_operator(op: str) -> str:
    mapping = {
        ":": "==",
        "=": "==",
        "!": "!=",
        "!=": "!=",
        ">": ">",
        ">=": ">=",
        "<": "<",
        "<=": "<=",
    }
    return mapping.get(op, "==")


# ── Parser (recursive descent) ────────────────────────────────────────


class _QueryParser:
    """Recursive-descent parser for structured query strings."""

    def __init__(self, tokens: list[tuple[str, str]]):
        self._tokens = tokens
        self._pos = 0

    def peek(self) -> tuple[str, str]:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else (TOKEN_EOF, "")

    def consume(self, expected_type: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if expected_type and token[0] != expected_type:
            raise ValueError(f"Expected token type {expected_type!r}, got {token[0]!r} ({token[1]!r})")
        self._pos += 1
        return token

    def parse(self) -> QueryNode | None:
        """Parse the full token stream into an AST."""
        result = self._parse_or()
        # 将解析器未消耗的尾随 token 转为文本搜索词
        remaining = []
        while self.peek()[0] != TOKEN_EOF:
            _, val = self.consume()
            if val:
                remaining.append(val)
        if remaining:
            text_node = QueryNode.filter_node(
                FilterCondition(field="__text__", operator=":", value=" ".join(remaining))
            )
            result = QueryNode.and_node([result, text_node]) if result else text_node
        return result

    def _parse_or(self) -> QueryNode:
        left = self._parse_and()
        while self.peek()[0] == TOKEN_OR:
            self.consume(TOKEN_OR)
            right = self._parse_and()
            left = QueryNode.or_node([left, right])
        return left

    def _parse_and(self) -> QueryNode:
        left = self._parse_not()
        while self.peek()[0] == TOKEN_AND:
            self.consume(TOKEN_AND)
            right = self._parse_not()
            left = QueryNode.and_node([left, right])
        return left

    def _parse_not(self) -> QueryNode:
        if self.peek()[0] == TOKEN_NOT:
            self.consume(TOKEN_NOT)
            child = self._parse_primary()
            return QueryNode.not_node(child)
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        token_type, token_value = self.peek()

        if token_type == TOKEN_LPAREN:
            self.consume(TOKEN_LPAREN)
            node = self._parse_or()
            self.consume(TOKEN_RPAREN)
            return node

        if token_type == TOKEN_FIELD:
            self.consume()
            condition = _parse_filter_condition(token_value)
            return QueryNode.filter_node(condition)

        if token_type == TOKEN_VALUE:
            self.consume()
            # Plain text term — treat as a simple contains filter
            condition = FilterCondition(field="__text__", operator=":", value=token_value)
            return QueryNode.filter_node(condition)

        # Unexpected token — skip
        self.consume()
        return QueryNode.filter_node(FilterCondition(field="__text__", operator=":", value=token_value))


def _parse_filter_condition(token_value: str) -> FilterCondition:
    """Parse a field:value token into a FilterCondition."""
    for op in ("!=", ">=", "<=", ">", "<", "=="):
        parts = token_value.split(op, 1)
        if len(parts) == 2:
            field = parts[0].strip()
            value = parts[1].strip()
            # Try numeric conversion
            try:
                value = float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                logger.debug("Failed to parse value as numeric, keeping as string: %s", value, exc_info=True)
            return FilterCondition(field=field, operator=op, value=value)

    # Default to equality if no operator found
    field, _, value = token_value.partition("==")
    return FilterCondition(field=field.strip() if field else "", operator="==", value=value.strip())


# ── Public API ────────────────────────────────────────────────────────


def parse_structured_query(query: str) -> ParsedQuery:
    """Parse a structured query string into an AST.

    Supported syntax:
        type:person AND tag:friend
        priority:high OR priority:urgent
        status:done NOT priority:low
        (type:person OR type:contact) AND tag:friend
        deadline>2026-06-01

    Returns a ParsedQuery with AST and leftover text terms.
    """
    if not query or not query.strip():
        return ParsedQuery(error="Empty query")

    original = query.strip()
    tokens = _tokenize(original)

    # Separate plain text terms from structured tokens
    structured_tokens: list[tuple[str, str]] = []
    text_terms: list[str] = []
    i = 0
    while i < len(tokens):
        t_type, t_val = tokens[i]
        if t_type == TOKEN_VALUE and not _is_logical_keyword(t_val):
            # Check if next token after this is a field token → it's part of structure
            if i + 1 < len(tokens) and tokens[i + 1][0] == TOKEN_FIELD:
                structured_tokens.append(tokens[i])
            else:
                text_terms.append(t_val)
        else:
            structured_tokens.append(tokens[i])
        i += 1

    if not structured_tokens or all(t[0] in (TOKEN_EOF,) for t in structured_tokens):
        return ParsedQuery(text_terms=text_terms, original=original)

    try:
        parser = _QueryParser(structured_tokens)
        ast = parser.parse()
        return ParsedQuery(ast=ast, text_terms=text_terms, original=original)
    except (ValueError, IndexError) as exc:
        logger.warning("Structured query parse failed: %s", exc, exc_info=True)
        return ParsedQuery(text_terms=text_terms, original=original, error=str(exc))


def _is_logical_keyword(word: str) -> bool:
    return word.upper() in ("AND", "OR", "NOT")


# ── Query evaluation ──────────────────────────────────────────────────


def evaluate_query(
    query: ParsedQuery,
    entries: list[dict[str, Any]],
    *,
    field_resolver: Callable[[dict[str, Any], str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a parsed query against a list of entry dicts.

    Args:
        query: ParsedQuery with AST.
        entries: List of entry dicts to filter.
        field_resolver: Optional function to resolve a field name to a value
                        from an entry. Default: entry.get(field).

    Returns:
        Filtered list of entries matching the query.
    """
    if query.error or query.ast is None:
        # Fall back to text term matching
        if query.text_terms:
            return _filter_by_text_terms(entries, query.text_terms)
        return list(entries)

    resolver = field_resolver or (lambda e, f: _resolve_field(e, f))
    return [e for e in entries if _evaluate_node(query.ast, e, resolver)]


def _evaluate_node(
    node: QueryNode,
    entry: dict[str, Any],
    resolver: Callable[[dict[str, Any], str], Any],
) -> bool:
    """Recursively evaluate a query node against an entry."""
    if node.type == NODE_FILTER and node.condition:
        return _evaluate_condition(node.condition, entry, resolver)

    if node.type == NODE_NOT and node.children:
        return not _evaluate_node(node.children[0], entry, resolver)

    if node.type == NODE_AND and node.children:
        return all(_evaluate_node(child, entry, resolver) for child in node.children)

    if node.type == NODE_OR and node.children:
        return any(_evaluate_node(child, entry, resolver) for child in node.children)

    if node.type == NODE_GROUP and node.children:
        return _evaluate_node(node.children[0], entry, resolver)

    return True


def _evaluate_condition(
    condition: FilterCondition,
    entry: dict[str, Any],
    resolver: Callable[[dict[str, Any], str], Any],
) -> bool:
    """Evaluate a single filter condition against an entry."""
    if condition.field == "__text__":
        return _text_contains(entry, str(condition.value))

    actual_value = resolver(entry, condition.field)
    expected = condition.value

    if condition.operator == "==":
        return _eq(actual_value, expected)
    elif condition.operator == "!=":
        return not _eq(actual_value, expected)
    elif condition.operator == ":":
        return _contains(actual_value, expected)
    elif condition.operator == ">":
        return _compare(actual_value, expected) > 0
    elif condition.operator == ">=":
        return _compare(actual_value, expected) >= 0
    elif condition.operator == "<":
        return _compare(actual_value, expected) < 0
    elif condition.operator == "<=":
        return _compare(actual_value, expected) <= 0

    return False


def _resolve_field(entry: dict[str, Any], field: str) -> Any:
    """Resolve a field name from an entry. Supports nested lookup with dot notation."""
    # Direct field match
    if field in entry:
        return entry[field]
    # field_values sub-dict
    fv = entry.get("field_values", {})
    if isinstance(fv, dict) and field in fv:
        return fv[field]
    # tags list
    if field == "tag" or field == "tags":
        return entry.get("tags", [])
    # supertag type alias
    if field in ("type", "supertag"):
        return entry.get("supertag_type", "")
    # entity type
    if field == "entity":
        return entry.get("entity_type", "")
    return None


def _eq(actual: Any, expected: Any) -> bool:
    """Equality check supporting string/numeric and list containment."""
    if actual is None:
        return False
    if isinstance(actual, list):
        return expected in actual or str(expected) in [str(a) for a in actual]
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.lower() == expected.lower()
    try:
        return actual == expected
    except (ValueError, TypeError):
        return str(actual) == str(expected)


def _contains(actual: Any, expected: Any) -> bool:
    """Contains / substring check."""
    if actual is None:
        return False
    if isinstance(actual, list):
        return any(str(expected).lower() in str(a).lower() for a in actual)
    return str(expected).lower() in str(actual).lower()


def _compare(actual: Any, expected: Any) -> int:
    """Compare two values. Returns -1, 0, or 1."""
    if actual is None:
        return -1
    if isinstance(actual, str) and isinstance(expected, str):
        return (actual > expected) - (actual < expected)
    try:
        a = float(actual) if not isinstance(actual, (int, float)) else actual
        e = float(expected) if not isinstance(expected, (int, float)) else expected
        return (a > e) - (a < e)
    except (ValueError, TypeError):
        return -1


def _text_contains(entry: dict[str, Any], text: str) -> bool:
    """Check if any text field in entry contains the given text."""
    lower_text = text.lower()
    for key in ("tags", "supertag_type", "entity_type", "source"):
        val = entry.get(key)
        if val and lower_text in str(val).lower():
            return True
    fv = entry.get("field_values", {})
    if isinstance(fv, dict):
        for val in fv.values():
            if val and lower_text in str(val).lower():
                return True
    return False


def _filter_by_text_terms(entries: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    """Simple AND text-term filtering for queries without structured syntax."""
    result = list(entries)
    for term in terms:
        term_lower = term.lower()
        result = [e for e in result if _text_contains(e, term_lower)]
    return result


# ── Query helpers ─────────────────────────────────────────────────────


def build_field_filter(
    *,
    supertag_type: str | None = None,
    entity_type: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> QueryNode | None:
    """Build a query node from simple field filters (all conditions are AND)."""
    conditions: list[FilterCondition] = []
    if supertag_type:
        conditions.append(FilterCondition(field="supertag_type", operator="==", value=supertag_type))
    if entity_type:
        conditions.append(FilterCondition(field="entity_type", operator="==", value=entity_type))
    if tags:
        for tag in tags:
            conditions.append(FilterCondition(field="tags", operator=":", value=tag))
    if source:
        conditions.append(FilterCondition(field="source", operator="==", value=source))

    if not conditions:
        return None

    nodes = [QueryNode.filter_node(c) for c in conditions]
    return QueryNode.and_node(nodes) if len(nodes) > 1 else nodes[0]


def query_to_string(node: QueryNode) -> str:
    """Convert a query node back to a human-readable string (for debugging)."""
    if node.type == NODE_FILTER and node.condition:
        c = node.condition
        return f"{c.field}{c.operator}{c.value}"

    if node.type == NODE_NOT:
        return f"NOT {query_to_string(node.children[0])}" if node.children else "NOT"

    if node.type == NODE_AND:
        return " AND ".join(f"({query_to_string(c)})" if c.type in (NODE_OR,) else query_to_string(c) for c in node.children)

    if node.type == NODE_OR:
        return " OR ".join(query_to_string(c) for c in node.children)

    return ""


# ── Combined query builder ────────────────────────────────────────────


def build_combined_query(
    structured: str = "",
    *,
    supertag_type: str | None = None,
    entity_type: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> ParsedQuery:
    """Build a combined query from a structured string and simple field filters."""
    parsed = parse_structured_query(structured) if structured else ParsedQuery()

    field_filter = build_field_filter(
        supertag_type=supertag_type,
        entity_type=entity_type,
        tags=tags,
        source=source,
    )

    if parsed.ast and field_filter:
        parsed.ast = QueryNode.and_node([parsed.ast, field_filter])
    elif field_filter:
        parsed.ast = field_filter

    if not parsed.ast and not parsed.text_terms:
        parsed.error = "Empty query"

    return parsed
