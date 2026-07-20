"""
MySQL MCP Server — v2
基于 Model Context Protocol 的 MySQL 数据库操作服务器

v2 增强：
- DBConnectionPool: 每数据库连接池复用，避免频繁握手
- 事务支持: begin_transaction / commit / rollback（基于连接会话）
- 元数据浏览: 视图 / 存储过程 / 函数 / 触发器
- ER 关系总览: 跨表外键关系
- EXPLAIN 执行计划分析: 自动解读
- 结果导出: CSV / JSON 文件
- 进程管理: kill_query
- 查询历史: 本会话最近 SQL
- 审计日志: SQL 执行落盘
- MCP Prompts: generate_query / optimize_sql 等预置提示词
- 修复 Resource 模板: 三张资源表全部正确注册
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pymysql
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mysql-mcp")

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "")
MYSQL_CHARSET = os.getenv("MYSQL_CHARSET", "utf8mb4")

READ_ONLY = os.getenv("READ_ONLY", "false").lower() == "true"
MAX_RESULT_ROWS = int(os.getenv("MAX_RESULT_ROWS", "1000"))
QUERY_TIMEOUT = int(os.getenv("QUERY_TIMEOUT", "30"))
ALLOW_DDL = os.getenv("ALLOW_DDL", "false").lower() == "true"

POOL_SIZE = int(os.getenv("POOL_SIZE", "5"))
POOL_MAX_OVERFLOW = int(os.getenv("POOL_MAX_OVERFLOW", "5"))
AUDIT_LOG = os.getenv("AUDIT_LOG", "true").lower() == "true"
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "./exports")).resolve()
QUERY_HISTORY_LIMIT = int(os.getenv("QUERY_HISTORY_LIMIT", "50"))

EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# SQL 安全分类
# ---------------------------------------------------------------------------
READ_ONLY_KEYWORDS = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "LOAD", "TRUNCATE", "CALL", "SET", "USE"}
DDL_KEYWORDS = {"CREATE", "ALTER", "DROP", "RENAME", "INDEX", "VIEW", "TRUNCATE"}
DANGEROUS_KEYWORDS = {"GRANT", "REVOKE", "FLUSH", "SHUTDOWN", "KILL", "RESET"}


def _classify_sql(sql: str) -> str:
    cleaned = re.sub(r"--.*?(\n|$)", " ", sql)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL).strip()
    if not cleaned:
        return "empty"
    first = cleaned.split(None, 1)[0].upper()
    # 去首尾标点（包裹符）
    first = first.strip('"`[]()')
    if first in READ_ONLY_KEYWORDS:
        return "read"
    if first in DANGEROUS_KEYWORDS:
        return "dangerous"
    if first in DDL_KEYWORDS:
        return "ddl"
    if first in WRITE_KEYWORDS:
        return "write"
    return "write"


def _guard(sql: str) -> Optional[str]:
    """返回错误信息（若不允许），否则 None"""
    t = _classify_sql(sql)
    if t == "dangerous":
        return f"禁止执行危险语句（{t.upper()}）"
    if t == "ddl" and not ALLOW_DDL:
        return "DDL 语句（CREATE/ALTER/DROP）已禁用，如需启用请设置 ALLOW_DDL=true"
    if t in ("write", "ddl") and READ_ONLY:
        return f"当前为只读模式，不允许执行 {t.upper()} 语句"
    if t == "empty":
        return "SQL 语句不能为空"
    return None


# ---------------------------------------------------------------------------
# 连接池（简单实现，按 database 分桶）
# ---------------------------------------------------------------------------
class PooledConnection:
    """包装连接，close() 时归还而非真正关闭"""
    def __init__(self, pool: "DBConnectionPool", conn: pymysql.Connection, db_key: str):
        self._pool = pool
        self._conn = conn
        self._db_key = db_key
        self._released = False
        self.last_used = time.time()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def real_close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def release(self):
        if not self._released:
            self._released = True
            self._pool._return(self._db_key, self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
            self.real_close()
            self._released = True
        else:
            self.release()


class DBConnectionPool:
    def __init__(self):
        self._pools: dict[str, list[PooledConnection]] = {}
        self._lock = threading.Lock()
        self._stats = {"created": 0, "reused": 0, "closed": 0}

    def _new_conn(self, database: Optional[str]) -> pymysql.Connection:
        # 未显式指定时回退到环境变量默认库
        target_db = database if database is not None else MYSQL_DATABASE
        kwargs = dict(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, charset=MYSQL_CHARSET,
            autocommit=False, connect_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
        )
        if target_db:
            kwargs["database"] = target_db
        self._stats["created"] += 1
        return pymysql.connect(**kwargs)

    def acquire(self, database: Optional[str] = None) -> PooledConnection:
        target_db = database if database is not None else MYSQL_DATABASE
        db_key = target_db or "__default__"
        with self._lock:
            bucket = self._pools.setdefault(db_key, [])
            while bucket:
                pc = bucket.pop()
                try:
                    pc._conn.ping(reconnect=False)
                    # 确保连接在正确的库上
                    if target_db:
                        with pc._conn.cursor() as cur:
                            cur.execute("SELECT DATABASE() AS db")
                            if cur.fetchone()["db"] != target_db:
                                cur.execute(f"USE `{target_db}`")
                    self._stats["reused"] += 1
                    pc.last_used = time.time()
                    return pc
                except Exception:
                    pc.real_close()
                    self._stats["closed"] += 1
            # 无可用连接，新建
            conn = self._new_conn(database)
        try:
            conn.ping(reconnect=True)
        except Exception:
            pass
        return PooledConnection(self, conn, db_key)

    def _return(self, db_key: str, pc: PooledConnection):
        try:
            # 归还前重置会话状态
            pc._conn.rollback()
            with pc._conn.cursor() as cur:
                cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (QUERY_TIMEOUT * 1000,))
        except Exception:
            pc.real_close()
            self._stats["closed"] += 1
            return
        with self._lock:
            bucket = self._pools.setdefault(db_key, [])
            if len(bucket) < POOL_SIZE:
                bucket.append(pc)
            else:
                pc.real_close()
                self._stats["closed"] += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                **self._stats,
                "idle": sum(len(v) for v in self._pools.values()),
                "perDb": {k: len(v) for k, v in self._pools.items()},
            }

    def close_all(self):
        with self._lock:
            for bucket in self._pools.values():
                for pc in bucket:
                    pc.real_close()
            self._pools.clear()


POOL = DBConnectionPool()

# ---------------------------------------------------------------------------
# 事务会话（按 session_id 保留连接）
# ---------------------------------------------------------------------------
_tx_conns: dict[str, PooledConnection] = {}
_tx_lock = threading.Lock()


def _tx_get(session_id: str) -> Optional[PooledConnection]:
    with _tx_lock:
        return _tx_conns.get(session_id)


def _tx_set(session_id: str, pc: PooledConnection):
    with _tx_lock:
        _tx_conns[session_id] = pc


def _tx_discard(session_id: str):
    with _tx_lock:
        pc = _tx_conns.pop(session_id, None)
        if pc:
            pc.real_close()


# ---------------------------------------------------------------------------
# 查询历史 + 审计日志
# ---------------------------------------------------------------------------
_query_history: deque[dict] = deque(maxlen=QUERY_HISTORY_LIMIT)
_history_lock = threading.Lock()
_audit_lock = threading.Lock()


def _record(sql: str, params: Optional[list], stmt_type: str, ok: bool, err: Optional[str], rows: int, duration_ms: float):
    entry = {
        "id": str(uuid.uuid4())[:8],
        "time": datetime.now().isoformat(timespec="seconds"),
        "sql": sql[:500],
        "params": params,
        "type": stmt_type,
        "ok": ok,
        "error": err,
        "rows": rows,
        "durationMs": round(duration_ms, 2),
    }
    with _history_lock:
        _query_history.append(entry)
    if AUDIT_LOG:
        log_path = EXPORT_DIR / "audit.log"
        line = json.dumps(entry, ensure_ascii=False)
        with _audit_lock, open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# 游标管理
# ---------------------------------------------------------------------------
@contextmanager
def get_cursor(database: Optional[str] = None, session_id: Optional[str] = None):
    """
    获取游标：
    - 若 session_id 对应的事务活跃，使用事务连接
    - 否则从连接池获取
    """
    tx_pc = _tx_get(session_id) if session_id else None
    if tx_pc:
        cur = tx_pc._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
        return

    pc = POOL.acquire(database)
    try:
        with pc._conn.cursor() as cur:
            cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (QUERY_TIMEOUT * 1000,))
            yield cur
        pc._conn.commit()
    except Exception:
        try:
            pc._conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pc.release()


def _format_results(rows: list[dict], columns: Optional[list] = None) -> dict:
    truncated = False
    if len(rows) > MAX_RESULT_ROWS:
        rows = rows[:MAX_RESULT_ROWS]
        truncated = True
    if not columns and rows:
        columns = list(rows[0].keys())
    elif not columns:
        columns = []
    # Decimal / datetime 等序列化成字符串
    return {
        "columns": columns,
        "rowCount": len(rows),
        "rows": rows,
        "truncated": truncated,
        "maxRows": MAX_RESULT_ROWS,
    }


def _execute(sql: str, params: Optional[list], database: Optional[str], session_id: Optional[str]):
    """内部执行入口：返回 (ok, result_or_error_dict)"""
    t0 = time.time()
    err = None
    try:
        with get_cursor(database=database, session_id=session_id) as cur:
            cur.execute(sql, params or ())
            stmt_type = _classify_sql(sql)
            if stmt_type == "read":
                rows = cur.fetchall()
                result = _format_results(rows)
                result["statementType"] = "read"
            else:
                last_id = cur.lastrowid
                result = {
                    "statementType": stmt_type,
                    "affectedRows": cur.rowcount,
                    "insertId": last_id if last_id else None,
                    "message": f"执行成功，影响 {cur.rowcount} 行",
                }
            _record(sql, params, stmt_type, True, None,
                    result.get("rowCount", result.get("affectedRows", 0)),
                    (time.time() - t0) * 1000)
            return True, result
    except Exception as e:
        err = str(e)
        _record(sql, params, _classify_sql(sql), False, err, 0, (time.time() - t0) * 1000)
        return False, {"error": err}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="MySQL MCP Server v2",
    instructions=(
        "你是一个 MySQL 数据库助手。通过 MCP 工具与 MySQL 数据库交互。"
        f"当前模式: {'只读' if READ_ONLY else '读写'}"
        f"{'，DDL 已禁用' if not ALLOW_DDL else ''}。"
        "提供连接池、事务、EXPLAIN 分析、CSV/JSON 导出、ER 关系浏览等能力。"
    ),
)


# ===========================================================================
# 工具：连接 / 状态
# ===========================================================================
@mcp.tool()
def ping() -> dict:
    """测试 MySQL 数据库连接是否正常，返回服务器版本、当前库、连接池状态。"""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT VERSION() AS `version`, DATABASE() AS `current_db`, NOW() AS `server_time`")
            row = cur.fetchone()
            return {
                "status": "ok",
                "connected": True,
                "version": row["version"],
                "currentDatabase": row["current_db"],
                "serverTime": str(row["server_time"]),
                "host": f"{MYSQL_HOST}:{MYSQL_PORT}",
                "user": MYSQL_USER,
                "readOnly": READ_ONLY,
                "allowDDL": ALLOW_DDL,
                "poolStats": POOL.stats(),
            }
    except Exception as e:
        logger.error("连接失败: %s", e)
        return {"status": "error", "connected": False, "error": str(e)}


@mcp.tool()
def get_server_status() -> dict:
    """获取 MySQL 服务器关键状态指标（连接数、QPS、慢查询、流量、InnoDB 行操作等）。"""
    try:
        with get_cursor() as cur:
            cur.execute("SHOW GLOBAL STATUS")
            all_status = {r["Variable_name"]: r["Value"] for r in cur.fetchall()}
            keys = [
                "Connections", "Max_used_connections", "Threads_connected", "Threads_running",
                "Questions", "Queries", "Slow_queries", "Uptime", "Bytes_received", "Bytes_sent",
                "Innodb_rows_read", "Innodb_rows_inserted", "Innodb_rows_updated", "Innodb_rows_deleted",
                "Table_locks_waited", "Created_tmp_disk_tables", "Com_select", "Com_insert",
                "Com_update", "Com_delete", "Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests",
            ]
            return {"status": {k: all_status.get(k) for k in keys if k in all_status},
                    "totalVariables": len(all_status)}
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# 工具：元数据浏览
# ===========================================================================
@mcp.tool()
def list_databases() -> dict:
    """列出服务器上所有可访问的数据库（SCHEMAS），区分系统库与用户库。"""
    try:
        with get_cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
            databases = [list(r.values())[0] for r in rows]
            system_dbs = {"information_schema", "mysql", "performance_schema", "sys"}
            return {
                "databases": databases,
                "userDatabases": [db for db in databases if db not in system_dbs],
                "systemDatabases": [db for db in databases if db in system_dbs],
                "count": len(databases),
            }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_tables(database: Optional[str] = None) -> dict:
    """列出指定数据库中的所有表（含引擎、行数、大小、注释）。"""
    try:
        with get_cursor(database) as cur:
            cur.execute("SHOW TABLES")
            rows = cur.fetchall()
            table_key = list(rows[0].keys())[0] if rows else None
            tables = [r[table_key] for r in rows] if table_key else []
            table_info = []
            for t in tables:
                cur.execute("SHOW TABLE STATUS LIKE %s", (t,))
                info = cur.fetchone()
                if info:
                    table_info.append({
                        "name": info.get("Name"),
                        "engine": info.get("Engine"),
                        "rows": info.get("Rows"),
                        "dataLength": info.get("Data_length"),
                        "indexLength": info.get("Index_length"),
                        "comment": info.get("Comment") or "",
                        "createTime": str(info.get("Create_time")) if info.get("Create_time") else None,
                        "updateTime": str(info.get("Update_time")) if info.get("Update_time") else None,
                    })
            cur.execute("SELECT DATABASE() AS db")
            return {"database": cur.fetchone()["db"], "tables": tables, "details": table_info, "count": len(tables)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def describe_table(table: str, database: Optional[str] = None) -> dict:
    """查看表结构：字段、主键、索引、外键、建表SQL。"""
    try:
        with get_cursor(database) as cur:
            cur.execute("SELECT DATABASE() AS db")
            current_db = cur.fetchone()["db"]
            cur.execute(f"DESCRIBE `{table}`")
            columns = cur.fetchall()
            cur.execute(f"SHOW CREATE TABLE `{table}`")
            create_row = cur.fetchone()
            create_sql = list(create_row.values())[1] if create_row else ""
            cur.execute(f"SHOW INDEX FROM `{table}`")
            indexes_raw = cur.fetchall()
            indexes = {}
            for idx in indexes_raw:
                kn = idx["Key_name"]
                if kn not in indexes:
                    indexes[kn] = {"name": kn, "unique": not idx["Non_unique"], "columns": [], "type": idx["Index_type"]}
                indexes[kn]["columns"].append(idx["Column_name"])
            try:
                cur.execute("""
                    SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME,
                           k.REFERENCED_COLUMN_NAME, r.UPDATE_RULE, r.DELETE_RULE
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
                    JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r
                      ON k.CONSTRAINT_NAME = r.CONSTRAINT_NAME AND k.TABLE_SCHEMA = r.CONSTRAINT_SCHEMA
                    WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s
                      AND k.REFERENCED_TABLE_NAME IS NOT NULL
                """, (table,))
                foreign_keys = cur.fetchall()
            except Exception:
                foreign_keys = []
            cur.execute("SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, TABLE_COMMENT, ENGINE, AUTO_INCREMENT "
                        "FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
            meta = cur.fetchone() or {}
            return {
                "database": current_db, "table": table,
                "columns": columns,
                "primaryKey": [c["Field"] for c in columns if c["Key"] == "PRI"],
                "indexes": list(indexes.values()),
                "foreignKeys": foreign_keys,
                "createSQL": create_sql,
                "meta": meta,
            }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_views(database: Optional[str] = None) -> dict:
    """列出数据库中的所有视图。"""
    try:
        with get_cursor(database) as cur:
            cur.execute("SELECT DATABASE() AS db")
            current_db = cur.fetchone()["db"]
            cur.execute("""
                SELECT TABLE_NAME AS name, VIEW_DEFINITION AS definition,
                       IS_UPDATABLE AS updatable, SECURITY_TYPE AS security_type
                FROM INFORMATION_SCHEMA.VIEWS
                WHERE TABLE_SCHEMA = DATABASE()
            """)
            views = cur.fetchall()
            return {"database": current_db, "views": views, "count": len(views)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_routines(routine_type: str = "PROCEDURE", database: Optional[str] = None) -> dict:
    """
    列出存储过程或函数。

    Parameters:
        routine_type: PROCEDURE 或 FUNCTION
    """
    rtype = routine_type.upper()
    if rtype not in ("PROCEDURE", "FUNCTION"):
        return {"error": "routine_type 必须是 PROCEDURE 或 FUNCTION"}
    try:
        with get_cursor(database) as cur:
            cur.execute("SELECT DATABASE() AS db")
            current_db = cur.fetchone()["db"]
            cur.execute("""
                SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS type,
                       DATA_TYPE AS return_type, CREATED, LAST_ALTERED, ROUTINE_COMMENT AS comment
                FROM INFORMATION_SCHEMA.ROUTINES
                WHERE ROUTINE_SCHEMA = DATABASE() AND ROUTINE_TYPE = %s
            """, (rtype,))
            items = cur.fetchall()
            return {"database": current_db, "routineType": rtype, "items": items, "count": len(items)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_triggers(database: Optional[str] = None) -> dict:
    """列出数据库中的所有触发器。"""
    try:
        with get_cursor(database) as cur:
            cur.execute("SELECT DATABASE() AS db")
            current_db = cur.fetchone()["db"]
            cur.execute("""
                SELECT TRIGGER_NAME AS name, EVENT_OBJECT_TABLE AS table_name,
                       ACTION_TIMING AS timing, EVENT_MANIPULATION AS event,
                       ACTION_STATEMENT AS statement, CREATED
                FROM INFORMATION_SCHEMA.TRIGGERS
                WHERE TRIGGER_SCHEMA = DATABASE()
            """)
            triggers = cur.fetchall()
            return {"database": current_db, "triggers": triggers, "count": len(triggers)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def er_graph(database: Optional[str] = None) -> dict:
    """
    生成当前库的 ER 关系图数据（节点=表，边=外键关系）。
    返回 nodes（表，含主键列）和 edges（外键关系）。
    """
    try:
        with get_cursor(database) as cur:
            cur.execute("SELECT DATABASE() AS db")
            current_db = cur.fetchone()["db"]
            cur.execute("SHOW TABLES")
            tbl_key = [k for k in cur.fetchone().keys()][0] if cur.rowcount else None
            cur.execute("SHOW TABLES")
            tables = [r[tbl_key] for r in cur.fetchall()] if tbl_key else []
            nodes = []
            for t in tables:
                cur.execute(f"SHOW KEYS FROM `{t}` WHERE Key_name = 'PRIMARY'")
                pk_rows = cur.fetchall()
                pk_cols = [r["Column_name"] for r in pk_rows]
                cur.execute("SELECT COUNT(*) AS c FROM `%s`" % t)
                row_cnt = cur.fetchone()["c"]
                nodes.append({"table": t, "primaryKey": pk_cols, "rows": row_cnt})
            cur.execute("""
                SELECT k.TABLE_NAME AS from_table, k.COLUMN_NAME AS from_column,
                       k.REFERENCED_TABLE_NAME AS to_table, k.REFERENCED_COLUMN_NAME AS to_column,
                       k.CONSTRAINT_NAME AS fk_name, r.DELETE_RULE AS on_delete, r.UPDATE_RULE AS on_update
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
                JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r
                  ON k.CONSTRAINT_NAME = r.CONSTRAINT_NAME AND k.CONSTRAINT_SCHEMA = r.CONSTRAINT_SCHEMA
                WHERE k.TABLE_SCHEMA = DATABASE() AND k.REFERENCED_TABLE_NAME IS NOT NULL
            """)
            edges = cur.fetchall()
            return {"database": current_db, "nodes": nodes, "edges": edges,
                    "nodeCount": len(nodes), "edgeCount": len(edges)}
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# 工具：查询执行
# ===========================================================================
@mcp.tool()
def execute_query(
    sql: str,
    database: Optional[str] = None,
    params: Optional[list] = None,
    session_id: Optional[str] = None,
) -> dict:
    """
    执行 SQL（支持读/写/DDL，受安全策略限制）。

    Parameters:
        sql: SQL 语句，参数用 %s 占位符
        database: 目标数据库
        params: 参数列表（防 SQL 注入）
        session_id: 若在事务中传入会话ID
    """
    sql_s = sql.strip()
    if err := _guard(sql_s):
        # 事务模式下允许写操作（即使 READ_ONLY 也让事务内部检查）
        if session_id and _tx_get(session_id) and _classify_sql(sql_s) == "write":
            pass
        else:
            return {"error": err}
    return _execute(sql_s, params, database, session_id)[1]


@mcp.tool()
def execute_select(
    sql: str,
    database: Optional[str] = None,
    params: Optional[list] = None,
    limit: Optional[int] = None,
) -> dict:
    """
    执行只读 SELECT/SHOW/DESCRIBE/EXPLAIN（即使 ALLOW_DDL/READ_ONLY 配置有误也只允许读）。

    Parameters:
        sql: SELECT 语句
        database: 数据库
        params: 参数列表
        limit: 自定义结果行数限制（≤1000）
    """
    sql_s = sql.strip()
    first = sql_s.split(None, 1)[0].upper() if sql_s else ""
    if first not in READ_ONLY_KEYWORDS:
        return {"error": "execute_select 仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN 语句"}
    max_rows = min(limit or MAX_RESULT_ROWS, MAX_RESULT_ROWS)
    try:
        with get_cursor(database) as cur:
            cur.execute(sql_s, params or ())
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            columns = list(rows[0].keys()) if rows else []
            _record(sql_s, params, "read", True, None, len(rows), 0)
            return {"columns": columns, "rowCount": len(rows), "rows": rows,
                    "truncated": truncated, "maxRows": max_rows, "statementType": "read"}
    except Exception as e:
        _record(sql_s, params, "read", False, str(e), 0, 0)
        return {"error": str(e)}


@mcp.tool()
def get_table_data(
    table: str,
    database: Optional[str] = None,
    columns: str = "*",
    where: Optional[str] = None,
    order_by: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """
    快速获取表数据（封装 SELECT，避免手写 SQL）。

    Parameters:
        table: 表名
        columns: 列名，如 'id,name,email'，默认 '*'
        where: WHERE 条件（不含 WHERE 关键字），如 'id > 10 AND status = 1'
        order_by: 排序，如 'id DESC'
        limit: 返回行数（≤1000）
        offset: 偏移量
    """
    limit = min(max(limit, 1), MAX_RESULT_ROWS)
    offset = max(offset, 0)
    # 校验 columns/where/order_by 不包含分号防注入（简单防御）
    for chunk, name in [(columns, "columns"), (where or "", "where"), (order_by or "", "order_by")]:
        if ";" in chunk:
            return {"error": f"{name} 中不允许包含分号"}
    sql = f"SELECT {columns} FROM `{table}`"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    sql += f" LIMIT {limit} OFFSET {offset}"
    try:
        with get_cursor(database) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = list(rows[0].keys()) if rows else (columns.split(",") if columns != "*" else [])
            return {"columns": cols, "rowCount": len(rows), "rows": rows, "limit": limit, "offset": offset}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def count_rows(table: str, database: Optional[str] = None, where: Optional[str] = None) -> dict:
    """统计表的行数，支持可选 WHERE 条件。"""
    sql = f"SELECT COUNT(*) AS cnt FROM `{table}`"
    if where:
        if ";" in where:
            return {"error": "where 中不允许包含分号"}
        sql += f" WHERE {where}"
    try:
        with get_cursor(database) as cur:
            cur.execute(sql)
            return {"table": table, "count": cur.fetchone()["cnt"]}
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# 工具：EXPLAIN 分析 / 查询历史 / 导出 / 进程管理
# ===========================================================================
@mcp.tool()
def explain_query(sql: str, database: Optional[str] = None, params: Optional[list] = None, format: str = "traditional") -> dict:
    """
    分析 SQL 执行计划，返回 EXPLAIN 结果并给出中文解读。

    Parameters:
        sql: 要分析的 SELECT 语句
        format: traditional / json
    """
    fmt = format.upper()
    if fmt not in ("TRADITIONAL", "JSON"):
        return {"error": "format 必须是 traditional 或 json"}
    sql_s = sql.strip().rstrip(";")
    explain_sql = f"EXPLAIN {('FORMAT=' + fmt + ' ') if fmt == 'JSON' else ''}{sql_s}"
    try:
        with get_cursor(database) as cur:
            cur.execute(explain_sql, params or ())
            rows = cur.fetchall()
            insights = []
            for r in rows:
                # 常用字段解读
                if "type" in r and r["type"] == "ALL":
                    insights.append(f"表 `{r.get('table')}` 发生全表扫描(ALL)，建议检查索引")
                if "key" in r and not r["key"] and r.get("type") != "ALL":
                    insights.append(f"表 `{r.get('table')}` 未使用索引(key=NULL)")
                if "Extra" in r and r["Extra"]:
                    extra = r["Extra"]
                    if "Using filesort" in extra:
                        insights.append(f"表 `{r.get('table')}` 使用 filesort，考虑添加合适的排序索引")
                    if "Using temporary" in extra:
                        insights.append(f"表 `{r.get('table')}` 使用临时表，注意 GROUP BY/ORDER BY 优化")
                    if "Using index condition" in extra or "Using index" in extra:
                        insights.append(f"表 `{r.get('table')}` 使用了索引覆盖/条件下推 ✓")
                if "rows" in r and isinstance(r["rows"], int) and r["rows"] > 100000:
                    insights.append(f"表 `{r.get('table')}` 预估扫描行数 {r['rows']}，数据量大请考虑索引优化")
            return {
                "sql": sql_s,
                "format": fmt,
                "explain": rows,
                "insights": insights or ["未发现明显问题"],
            }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def export_query(
    sql: str,
    filename: str,
    format: str = "csv",
    database: Optional[str] = None,
    params: Optional[list] = None,
) -> dict:
    """
    执行查询并把结果导出为 CSV 或 JSON 文件，返回本地文件路径。

    Parameters:
        sql: SELECT 语句
        filename: 文件名（不含扩展名），自动加时间戳避免覆盖
        format: csv / json
        database: 数据库
        params: 参数列表
    """
    fmt = format.lower()
    if fmt not in ("csv", "json"):
        return {"error": "format 必须是 csv 或 json"}
    sql_s = sql.strip()
    t = _classify_sql(sql_s)
    if t != "read":
        return {"error": "export_query 仅允许 SELECT 语句"}
    try:
        with get_cursor(database) as cur:
            cur.execute(sql_s, params or ())
            rows = cur.fetchall()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r"[^\w\-]+", "_", filename).strip("_") or "export"
            out_path = EXPORT_DIR / f"{safe_name}_{ts}.{fmt}"
            if fmt == "csv":
                columns = list(rows[0].keys()) if rows else []
                with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=columns)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
            else:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
            return {
                "success": True,
                "format": fmt,
                "rowCount": len(rows),
                "path": str(out_path),
                "filename": out_path.name,
                "size": out_path.stat().st_size,
            }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def show_processlist() -> dict:
    """查看当前 MySQL 进程列表（SHOW FULL PROCESSLIST）。"""
    try:
        with get_cursor() as cur:
            cur.execute("SHOW FULL PROCESSLIST")
            rows = cur.fetchall()
            return {"processes": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def kill_query(process_id: int) -> dict:
    """
    Kill 指定进程（需要 SUPER 权限）。管理员操作，会被审计。

    Parameters:
        process_id: SHOW PROCESSLIST 里的 Id
    """
    if READ_ONLY:
        return {"error": "只读模式下不允许 kill 进程"}
    try:
        with get_cursor() as cur:
            cur.execute("KILL %s", (process_id,))
            return {"success": True, "killedId": process_id, "message": f"进程 {process_id} 已终止"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_query_history(limit: int = 20) -> dict:
    """查看本会话最近执行过的 SQL 历史（时间、耗时、结果、错误）。"""
    with _history_lock:
        items = list(_query_history)[-limit:]
    return {"history": items, "count": len(items)}


# ===========================================================================
# 工具：事务
# ===========================================================================
@mcp.tool()
def begin_transaction(database: Optional[str] = None) -> dict:
    """
    开启一个新事务，返回 session_id。后续在 execute_query 中传入 session_id 即可在同一事务里执行多语句。
    记得在结束时调用 commit_transaction 或 rollback_transaction。

    ⚠️ 事务会独占一条连接，请在操作完毕后及时 commit/rollback。
    """
    if READ_ONLY:
        return {"error": "只读模式下不允许事务"}
    session_id = str(uuid.uuid4())[:12]
    pc = POOL.acquire(database)
    try:
        pc._conn.begin()
        _tx_set(session_id, pc)
        with pc._conn.cursor() as cur:
            cur.execute("SELECT DATABASE() AS db, CONNECTION_ID() AS conn_id")
            row = cur.fetchone()
        return {
            "success": True,
            "sessionId": session_id,
            "database": row["db"],
            "connectionId": row["conn_id"],
            "message": "事务已开启，后续 execute_query 请带上 session_id",
        }
    except Exception as e:
        pc.real_close()
        return {"error": str(e)}


@mcp.tool()
def commit_transaction(session_id: str) -> dict:
    """提交指定事务。"""
    pc = _tx_get(session_id)
    if not pc:
        return {"error": f"未找到会话 {session_id}"}
    try:
        pc._conn.commit()
        return {"success": True, "sessionId": session_id, "message": "事务已提交"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        _tx_discard(session_id)


@mcp.tool()
def rollback_transaction(session_id: str) -> dict:
    """回滚指定事务。"""
    pc = _tx_get(session_id)
    if not pc:
        return {"error": f"未找到会话 {session_id}"}
    try:
        pc._conn.rollback()
        return {"success": True, "sessionId": session_id, "message": "事务已回滚"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        _tx_discard(session_id)


# ===========================================================================
# Resources
# ===========================================================================
@mcp.resource("mysql://databases")
def resource_databases() -> str:
    """所有数据库列表"""
    return json.dumps(list_databases(), ensure_ascii=False, indent=2)


@mcp.resource("mysql://tables/{database}")
def resource_tables(database: str) -> str:
    """指定数据库的表列表"""
    return json.dumps(list_tables(database=database), ensure_ascii=False, indent=2)


@mcp.resource("mysql://schema/{database}/{table}")
def resource_schema(database: str, table: str) -> str:
    """指定表的结构"""
    return json.dumps(describe_table(table=table, database=database), ensure_ascii=False, indent=2)


@mcp.resource("mysql://er/{database}")
def resource_er(database: str) -> str:
    """数据库 ER 关系（JSON 图数据）"""
    return json.dumps(er_graph(database=database), ensure_ascii=False, indent=2)


# ===========================================================================
# Prompts（预置提示词模板）
# ===========================================================================
@mcp.prompt()
def generate_query(description: str, tables: str) -> str:
    """
    根据自然语言需求和相关表信息生成 SQL 查询。

    Parameters:
        description: 要查询的问题描述（自然语言）
        tables: 相关表名，多个用逗号分隔
    """
    return f"""你是资深 MySQL DBA，请根据用户需求生成安全、高效的 SQL。

用户需求：{description}
相关表：{tables}

要求：
1. 先查看这些表的结构（describe_table），确认字段名、类型、索引情况
2. 必要时调用 er_graph 了解表间关系
3. 生成 SQL 后先用 explain_query 分析执行计划，确保索引使用合理
4. 若数据量大，必须加 LIMIT 限制返回行数
5. 使用参数化占位符 %s，避免字符串拼接
6. 不要使用 SELECT *，明确列出需要的字段
7. 给出 SQL 并简要解释其逻辑和性能考虑
"""


@mcp.prompt()
def optimize_sql(sql: str) -> str:
    """
    优化现有 SQL 的性能。

    Parameters:
        sql: 需要优化的 SQL 语句
    """
    return f"""请优化以下 MySQL 查询的性能：

```sql
{sql}
```

优化步骤：
1. 调用 explain_query 分析当前执行计划
2. 识别全表扫描、filesort、temporary table 等性能问题
3. 根据表结构（describe_table）和索引情况提出优化建议
4. 给出优化后的 SQL
5. 用 explain_query 对比优化前后执行计划
6. 列出新增的索引建议（如需要）
"""


@mcp.prompt()
def compare_schema(table_a: str, table_b: str, database: str = "") -> str:
    """
    对比两张表的结构差异，可用于数据迁移/合并场景。

    Parameters:
        table_a: 表A
        table_b: 表B
        database: 数据库
    """
    db_clause = f" 在 {database} 库中" if database else ""
    return f"""请对比 `{table_a}` 和 `{table_b}` 两张表的结构差异{db_clause}：

1. 分别调用 describe_table 获取两张表结构
2. 对比：
   - 两张表各自独有字段
   - 共有字段但类型/长度/默认值/可空性不同
   - 索引差异（主键、唯一、普通索引）
   - 外键差异
3. 输出结构化对比报告
"""


@mcp.prompt()
def database_summary(database: str = "") -> str:
    """
    对数据库做一份整体概览报告（大小、行数、关系、健康度）。

    Parameters:
        database: 数据库名，留空则使用当前数据库
    """
    return f"""请对数据库 `{database or '(当前库)'}` 生成一份整体概览报告：

1. list_tables 获取全部表
2. 对每张表：describe_table（结构+索引）+ count_rows（行数）
3. er_graph 获取外键关系
4. get_server_status 获取服务器健康状态
5. 汇总输出：
   - 数据库规模（表数量、总行数、Top 10 大表）
   - 关系结构图（文字描述或 Mermaid）
   - 缺少索引的字段建议
   - 潜在的性能/设计问题
"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    logger.info(
        "启动 MySQL MCP Server v2 | %s@%s:%s/%s | 模式=%s DDL=%s 传输=%s 导出目录=%s",
        MYSQL_USER, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE or "(无)",
        "只读" if READ_ONLY else "读写",
        "允许" if ALLOW_DDL else "禁止",
        transport, EXPORT_DIR,
    )
    try:
        if transport == "stdio":
            mcp.run(transport="stdio")
        elif transport == "sse":
            mcp.run(transport="sse", host=host, port=port)
        elif transport == "streamable-http":
            mcp.run(transport="streamable-http", host=host, port=port)
        else:
            logger.error("不支持的传输模式: %s", transport)
            sys.exit(1)
    finally:
        POOL.close_all()


if __name__ == "__main__":
    main()
