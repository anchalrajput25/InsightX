# backend/text_to_sql/sql_safety.py
#
# Deterministic read-only safety gate for generated SQL.
#
# The LLM is never trusted: every query must pass this check before it
# touches the database (it runs before EXPLAIN validation). Decisions are
# made on the parsed AST wherever possible; the keyword check only sees
# SQL with string literals, quoted identifiers and comments removed, so
# values such as WHERE status = 'set' are not mistaken for commands.
#
# Execution adds a second, independent layer (read-only transaction,
# statement_timeout, row cap) in query_executor.py.

import re
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

from backend import dialects
from backend.config import query_settings


# ============================================================
# POLICY
# ============================================================

ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Statement / sub-expression types that write, change session state or
# run arbitrary commands. getattr keeps this compatible across sqlglot
# versions that do not define every class.
_FORBIDDEN_NODE_NAMES = {
    "Insert": "INSERT",
    "Update": "UPDATE",
    "Delete": "DELETE",
    "Merge": "MERGE",
    "Create": "CREATE",
    "Drop": "DROP",
    "Alter": "ALTER",
    "TruncateTable": "TRUNCATE",
    "Grant": "GRANT",
    "Revoke": "REVOKE",
    "Copy": "COPY",
    "Set": "SET",
    "Transaction": "TRANSACTION",
    "Commit": "COMMIT",
    "Rollback": "ROLLBACK",
    "Command": "COMMAND",
    "Into": "SELECT INTO",
    "Pragma": "PRAGMA",
    "Attach": "ATTACH",
    "Detach": "DETACH",
}
FORBIDDEN_NODE_TYPES = {
    getattr(exp, class_name): label
    for class_name, label in _FORBIDDEN_NODE_NAMES.items()
    if hasattr(exp, class_name)
}

# Functions an analytics query never needs: file system access, sleeps,
# session/server control, sequence mutation, remote connections and
# server/user disclosure.
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "set_config",
    "nextval", "setval",
    "pg_notify",
    "query_to_xml", "query_to_xml_and_xmlschema",
    "cursor_to_xml", "table_to_xml", "table_to_xml_and_xmlschema",
    "version", "current_version",
    "current_user", "session_user", "user",
    "inet_server_addr", "inet_server_port",
    "inet_client_addr", "inet_client_port",
    # MySQL / MariaDB
    "sleep", "benchmark", "load_file", "get_lock", "release_lock",
    "release_all_locks", "is_free_lock", "is_used_lock", "master_pos_wait",
    "source_pos_wait", "sys_exec", "sys_eval", "connection_id",
    # SQL Server
    "openrowset", "openquery", "opendatasource", "openxml",
    "suser_sname", "suser_name", "system_user", "host_name",
    # SQLite
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
}
FORBIDDEN_FUNCTION_PREFIXES = (
    "pg_read", "pg_ls_", "pg_stat_file", "pg_file",
    "lo_", "dblink",
    "pg_terminate", "pg_cancel", "pg_reload", "pg_rotate",
    "pg_switch", "pg_promote", "pg_advisory", "pg_try_advisory",
    "pg_create", "pg_drop", "pg_replication", "pg_logical",
    "pg_backup", "pg_start_backup", "pg_stop_backup",
    # SQL Server stored procedures and server-side file readers
    "xp_", "sp_", "fn_xe_", "fn_trace_", "fn_get_audit",
)

# SQL Server's old system views, reachable without a schema name.
MSSQL_SYSTEM_TABLES = {
    "sysobjects", "syscolumns", "sysdatabases", "syslogins", "sysusers", "sysprocesses",
    "syscomments", "sysindexes", "sysxlogins", "sysservers", "sysfiles", "sysmessages",
}

# Defense in depth for statements sqlglot may not model. Applied only to
# SQL with literals/identifiers/comments removed.
FORBIDDEN_KEYWORDS = {
    "TRUNCATE": r"\bTRUNCATE\b",
    "MERGE": r"\bMERGE\s+INTO\b",
    "GRANT": r"\bGRANT\b",
    "REVOKE": r"\bREVOKE\b",
    "CALL": r"\bCALL\b",
    "DO": r"\bDO\b",
    "COPY": r"\bCOPY\b",
    "VACUUM": r"\bVACUUM\b",
    "ANALYZE": r"^\s*ANALYZE\b",
    "SET": r"\bSET\b",
    "RESET": r"\bRESET\b",
    "DISCARD": r"\bDISCARD\b",
    "BEGIN": r"\bBEGIN\b",
    "COMMIT": r"\bCOMMIT\b",
    "ROLLBACK": r"\bROLLBACK\b",
    "SAVEPOINT": r"\bSAVEPOINT\b",
    "LISTEN": r"\bLISTEN\b",
    "NOTIFY": r"\bNOTIFY\b",
    "PREPARE": r"\bPREPARE\b",
    "EXECUTE": r"\bEXECUTE\b",
    "DEALLOCATE": r"\bDEALLOCATE\b",
    # MySQL / MariaDB
    "INTO OUTFILE": r"\bINTO\s+(?:OUT|DUMP)FILE\b",
    "LOAD DATA": r"\bLOAD\s+(?:DATA|XML)\b",
    "HANDLER": r"\bHANDLER\b",
    "LOCK TABLES": r"\b(?:UN)?LOCK\s+TABLES?\b",
    "FLUSH": r"\bFLUSH\b",
    "KILL": r"\bKILL\b",
    "SHUTDOWN": r"\bSHUTDOWN\b",
    "INSTALL": r"\bINSTALL\s+(?:PLUGIN|SONAME)\b",
    "REPLACE INTO": r"\bREPLACE\s+INTO\b",
    # SELECT ... INTO creates a table (PostgreSQL, SQL Server)
    "INTO": r"\bINTO\b",
    # SQL Server
    "EXEC": r"\bEXEC\b",
    "WAITFOR": r"\bWAITFOR\b",
    "DBCC": r"\bDBCC\b",
    "RECONFIGURE": r"\bRECONFIGURE\b",
    "BULK": r"\bBULK\b",
    "OPENROWSET": r"\bOPEN(?:ROWSET|QUERY|DATASOURCE|XML)\b",
    "BACKUP": r"\bBACKUP\b",
    "RESTORE": r"\bRESTORE\b",
    # SQLite
    "ATTACH": r"\bATTACH\b",
    "DETACH": r"\bDETACH\b",
    "PRAGMA": r"\bPRAGMA\b",
}


# ============================================================
# SQL CLEANING
# ============================================================

_FENCE_START_RE = re.compile(r"^```[ \t]*(?:sql|postgresql|postgres)?[ \t]*", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"\s*```$")


def clean_sql(sql: str) -> str:
    """Remove markdown code fences and surrounding whitespace."""
    if not isinstance(sql, str):
        return ""
    sql = _FENCE_START_RE.sub("", sql.strip())
    sql = _FENCE_END_RE.sub("", sql)
    return sql.strip()


def strip_literals_and_comments(sql: str) -> str:
    """
    Return SQL with string literals, dollar-quoted strings, quoted
    identifiers and comments replaced by neutral placeholders.

    Used so keyword checks only look at real SQL syntax.
    """
    out: List[str] = []
    i, n = 0, len(sql)
    # MySQL: backslash escapes in strings, "#" line comments.
    mysql = dialects.current() == dialects.MYSQL
    # SQL Server: [bracketed identifiers] with ]] escapes.
    brackets = dialects.current() == dialects.MSSQL

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # # line comment (MySQL)
        if mysql and ch == "#":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue

        # `quoted identifier` (MySQL) with `` escapes
        if ch == "`":
            i += 1
            while i < n:
                if sql[i] == "`":
                    if i + 1 < n and sql[i + 1] == "`":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append('"_"')
            continue

        if brackets and ch == "[":
            i += 1
            while i < n:
                if sql[i] == "]":
                    if i + 1 < n and sql[i + 1] == "]":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append('"_"')
            continue

        # -- line comment
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue

        # /* block comment */ (PostgreSQL allows nesting)
        if ch == "/" and nxt == "*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
            continue

        # 'string' with '' escapes; E'...' also allows backslash escapes
        if ch == "'" or (mysql and ch == '"'):
            quote_char = ch
            backslash_escapes = mysql or (i > 0 and sql[i - 1] in "eE")
            i += 1
            while i < n:
                if backslash_escapes and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == quote_char:
                    if i + 1 < n and sql[i + 1] == quote_char:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")
            continue

        # "quoted identifier" with "" escapes
        if ch == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append('"_"')
            continue

        # $tag$ dollar-quoted string $tag$
        if ch == "$":
            match = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if match:
                tag = match.group(0)
                end = sql.find(tag, i + len(tag))
                i = n if end == -1 else end + len(tag)
                out.append("''")
                continue

        out.append(ch)
        i += 1

    return "".join(out)


# ============================================================
# PARSING
# ============================================================

def parse_statements(sql: str) -> List[exp.Expression]:
    """Parse SQL into its non-empty statements (dialect of the current question)."""
    try:
        statements = sqlglot.parse(sql, read=dialects.sqlglot_name())
    except Exception as exc:
        raise ValueError(f"{dialects.label()} SQL parsing failed: {exc}") from exc
    return [statement for statement in statements if statement is not None]


def parse_sql(sql: str) -> exp.Expression:
    """Parse exactly one SQL statement."""
    statements = parse_statements(sql)
    if not statements:
        raise ValueError("No SQL statement found.")
    if len(statements) > 1:
        raise ValueError("Only one SQL statement is allowed.")
    return statements[0]


def has_multiple_statements(sql: str) -> bool:
    """
    True when the SQL contains more than one statement.

    Uses the parser, so semicolons inside string literals do not count.
    Falls back to a literal-aware scan when the SQL does not parse.
    """
    sql = clean_sql(sql)
    if not sql:
        return False
    try:
        return len(parse_statements(sql)) > 1
    except ValueError:
        stripped = strip_literals_and_comments(sql).strip().rstrip(";")
        return ";" in stripped


def validate_root_query(ast: exp.Expression) -> tuple[bool, str]:
    """Only SELECT-style queries (incl. set operations) are allowed."""
    if isinstance(ast, ALLOWED_ROOTS):
        return True, ""
    if isinstance(ast, exp.Subquery) and isinstance(ast.this, ALLOWED_ROOTS):
        return True, ""
    return False, f"Unsupported root SQL expression: {type(ast).__name__}"


# ============================================================
# AST CHECKS
# ============================================================

def find_forbidden_operations(ast: exp.Expression) -> List[str]:
    """Write/DDL/session operations anywhere in the tree (incl. CTEs)."""
    found = set()
    for node in ast.walk():
        for node_type, label in FORBIDDEN_NODE_TYPES.items():
            if isinstance(node, node_type):
                found.add(label)
        if isinstance(node, exp.Select) and node.args.get("into"):
            found.add("SELECT INTO")
        if isinstance(node, exp.Lock):
            found.add("ROW LOCK (FOR UPDATE/SHARE)")
    return sorted(found)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name or "").lower()
    return str(node.sql_name() or "").lower()


def find_forbidden_functions(ast: exp.Expression) -> List[str]:
    found = set()
    for node in ast.find_all(exp.Func):
        name = _function_name(node)
        if not name:
            continue
        if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            found.add(name)
    return sorted(found)


def find_forbidden_keywords(sql: str) -> List[str]:
    """Keyword scan over SQL with literals/identifiers/comments removed."""
    syntax_only = strip_literals_and_comments(sql)
    return sorted(
        operation
        for operation, pattern in FORBIDDEN_KEYWORDS.items()
        if re.search(pattern, syntax_only, flags=re.IGNORECASE)
    )


def _cte_names(ast: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in ast.find_all(exp.CTE)
        if cte.alias_or_name
    }


def find_disallowed_tables(
    ast: exp.Expression,
    allowed_schemas: Optional[tuple[str, ...]] = None,
) -> List[str]:
    """
    Tables outside the business schemas: other schemas (pg_catalog,
    information_schema, the checkpoint schema), cross-database
    references and pg_* system relations.
    """
    if allowed_schemas is None:
        allowed_schemas = dialects.allowed_schemas()
    cte_names = _cte_names(ast)
    disallowed = set()

    for table in ast.find_all(exp.Table):
        name = str(table.name or "")
        if not name:
            # Table-valued functions; covered by the function checks.
            continue
        schema = str(table.db or "").lower()
        catalog = str(table.catalog or "")
        qualified = ".".join(part for part in (catalog, schema, name) if part)

        if not schema and name.lower() in cte_names:
            continue
        own_database = dialects.database_name()
        if catalog and own_database and catalog.lower() == own_database.lower() and schema in allowed_schemas:
            continue  # SQL Server: shop.dbo.orders in database "shop"
        if catalog:
            disallowed.add(qualified)
        elif schema and schema not in allowed_schemas:
            disallowed.add(qualified)
        elif name.lower().startswith("pg_"):
            disallowed.add(qualified)
        elif schema in set().union(*dialects.SYSTEM_SCHEMAS.values()):
            disallowed.add(qualified)
        elif name.lower().startswith("sqlite_") or name.lower() in MSSQL_SYSTEM_TABLES:
            disallowed.add(qualified)

    return sorted(disallowed)


# ============================================================
# STRUCTURAL INFORMATION
# ============================================================

def extract_tables(ast: exp.Expression) -> List[str]:
    """Physical table references (CTE references excluded)."""
    cte_names = _cte_names(ast)
    return sorted({
        table.name
        for table in ast.find_all(exp.Table)
        if table.name and table.name.lower() not in cte_names
    })


def extract_columns(ast: exp.Expression) -> List[str]:
    return sorted({column.name for column in ast.find_all(exp.Column) if column.name})


def extract_ctes(ast: exp.Expression) -> List[str]:
    return sorted({cte.alias_or_name for cte in ast.find_all(exp.CTE) if cte.alias_or_name})


# ============================================================
# RESULT
# ============================================================

def safety_result(
    safe: bool,
    status: str,
    reason: str,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "safe": safe,
        "status": status,
        "reason": reason,
        "errors": errors or [],
        "warnings": warnings or [],
        "details": details or {},
    }


def _unsafe(reason: str, errors: List[str], details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return safety_result(safe=False, status="UNSAFE", reason=reason, errors=errors, details=details)


# ============================================================
# MAIN CHECK
# ============================================================

def check_sql_safety(sql: str) -> Dict[str, Any]:
    """
    Read-only safety gate. Rejects:
      1. empty SQL / multiple statements / unparseable SQL
      2. non-SELECT roots
      3. write, DDL, session, transaction and command statements
         anywhere in the tree (incl. data-modifying CTEs)
      4. SELECT INTO and row locks (FOR UPDATE/SHARE)
      5. dangerous functions (file access, sleep, settings, ...)
      6. tables outside the allowed schemas or pg_* system tables
      7. forbidden keywords in the SQL syntax (literals excluded)
    """
    sql = clean_sql(sql)
    if not sql:
        return _unsafe("SQL is empty.", ["Empty SQL query."])

    try:
        statements = parse_statements(sql)
    except ValueError as exc:
        return _unsafe("SQL parsing failed.", [str(exc)])

    if not statements:
        return _unsafe("SQL is empty.", ["No SQL statement found."])
    if len(statements) > 1:
        return _unsafe("Multiple SQL statements detected.", ["Only one SQL statement is allowed."])

    ast = statements[0]

    forbidden_operations = find_forbidden_operations(ast)
    if forbidden_operations:
        return _unsafe(
            "Forbidden SQL operation detected.",
            ["Forbidden operations: " + ", ".join(forbidden_operations)],
        )

    root_valid, root_reason = validate_root_query(ast)
    if not root_valid:
        return _unsafe(root_reason, [root_reason])

    forbidden_functions = find_forbidden_functions(ast)
    if forbidden_functions:
        return _unsafe(
            "Forbidden function call detected.",
            ["Functions not allowed in analytics queries: " + ", ".join(forbidden_functions)],
        )

    disallowed_tables = find_disallowed_tables(ast)
    if disallowed_tables:
        return _unsafe(
            "Query references tables outside the business schema.",
            [
                "Not allowed: " + ", ".join(disallowed_tables)
                + ". Allowed schemas: " + ", ".join(dialects.allowed_schemas())
            ],
        )

    forbidden_keywords = find_forbidden_keywords(sql)
    if forbidden_keywords:
        return _unsafe(
            "Forbidden SQL keyword detected.",
            ["Forbidden operations: " + ", ".join(forbidden_keywords)],
        )

    return safety_result(
        safe=True,
        status="SAFE",
        reason="SQL passed the read-only safety checks.",
        details={
            "tables": extract_tables(ast),
            "columns": extract_columns(ast),
            "ctes": extract_ctes(ast),
        },
    )


def is_safe_sql(sql: str) -> bool:
    return check_sql_safety(sql)["safe"]
