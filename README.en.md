# mysql-mcp-pro

> 🇨🇳 [中文版本](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green)](https://modelcontextprotocol.io)

A production-grade **MySQL MCP Server** that lets AI assistants (Claude Desktop, WorkBuddy, Cursor, Cline, etc.) interact with MySQL databases securely and efficiently through the standardized Model Context Protocol.

## ✨ Why mysql-mcp-pro?

Compared to other MySQL MCP implementations, this project offers a much richer feature set:

| Feature | mysql-mcp-pro | Others |
|---------|:---:|:---:|
| Connection pooling (eliminates handshake overhead) | ✅ | ❌ |
| Transactions (begin/commit/rollback) | ✅ | ❌ |
| Automatic EXPLAIN plan insights | ✅ | ❌ |
| CSV/JSON result export (Excel-friendly BOM) | ✅ | ❌ |
| ER graph data (nodes + edges) | ✅ | Few |
| Views / stored procs / functions / triggers | ✅ | Few |
| Query history & audit log | ✅ | ❌ |
| MCP Prompts (generate / optimize / compare / summary) | ✅ | ❌ |
| Safety tiers (read-only / DDL toggle / dangerous block / timeout / row cap / parameterized) | ✅ | Basic |
| Multiple transports (stdio / SSE / streamable-http) | ✅ | Some |

## 🚀 Quick Start

### 1. Install

```bash
git clone https://github.com/CCCT173/mysql-mcp-pro.git
cd mysql-mcp-pro
pip install -e .

# Or from PyPI (coming soon)
pip install mysql-mcp-pro
```

### 2. Configure

Copy `.env.example` to `.env` and edit:

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=your_database

READ_ONLY=false
ALLOW_DDL=false
MAX_RESULT_ROWS=1000
QUERY_TIMEOUT=30

POOL_SIZE=5
AUDIT_LOG=true
EXPORT_DIR=./exports
```

### 3. Register in your MCP client

#### Claude Desktop

On macOS, edit `~/Library/Application Support/Claude/claude_desktop_config.json`;
on Windows, `%APPDATA%/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mysql": {
      "command": "mysql-mcp-pro",
      "env": {
        "MYSQL_HOST": "localhost",
        "MYSQL_PORT": "3306",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "your_password",
        "MYSQL_DATABASE": "your_database",
        "READ_ONLY": "true"
      }
    }
  }
}
```

#### WorkBuddy / Cursor / Cline

Use the same JSON shape above in your client's MCP settings.

## 🛠 Tools (25)

### Connection & Status
- **`ping`** — test connection; returns server version + pool stats
- **`get_server_status`** — key MySQL metrics (connections, QPS, slow queries, traffic)

### Schema Browsing
- **`list_databases`** — all databases (system vs user)
- **`list_tables`** — tables with engine/rows/size/comment
- **`describe_table`** — columns, primary key, indexes, foreign keys, CREATE SQL
- **`list_views`** — views
- **`list_routines`** — stored procedures / functions
- **`list_triggers`** — triggers
- **`er_graph`** — cross-table foreign-key graph (nodes + edges) for ER diagrams

### Query Execution
- **`execute_query`** — run any SQL (read/write/DDL, safety-guarded), parameterized
- **`execute_select`** — read-only SELECT even if config is wrong
- **`get_table_data`** — quick data fetch with columns/where/order_by/limit/offset
- **`count_rows`** — row count with optional WHERE

### Analysis & Export
- **`explain_query`** — `EXPLAIN` plan with plain-language performance insights
- **`export_query`** — export results to CSV (UTF-8 BOM for Excel) or JSON
- **`list_query_history`** — session SQL history (duration/rows/errors)

### Transactions
- **`begin_transaction`** — start a new transaction, returns session_id
- **`commit_transaction`** — commit
- **`rollback_transaction`** — rollback

### Monitoring & Admin
- **`show_processlist`** — MySQL process list
- **`kill_query`** — kill a process (requires SUPER privilege)

### MCP Resources (4)
- `mysql://databases`
- `mysql://tables/{database}`
- `mysql://schema/{database}/{table}`
- `mysql://er/{database}`

### MCP Prompts (4)
- **`generate_query`** — natural language → SQL
- **`optimize_sql`** — analyze and optimize an existing SQL
- **`compare_schema`** — diff two table schemas
- **`database_summary`** — comprehensive database overview

## 🔒 Security Model

```
Dangerous (GRANT/REVOKE/SHUTDOWN/KILL/FLUSH/RESET) → always blocked
DDL (CREATE/ALTER/DROP/RENAME/TRUNCATE)            → gated by ALLOW_DDL
Writes (INSERT/UPDATE/DELETE)                       → gated by READ_ONLY
Reads (SELECT/SHOW/DESCRIBE/EXPLAIN)                → always allowed
```

- Parameterized queries (`%s` placeholders) prevent SQL injection
- Default 1000-row cap prevents accidental large result sets
- Server-side `MAX_EXECUTION_TIME` enforces query timeout
- All executed SQL is logged to `exports/audit.log`

## 📁 Project Structure

```
mysql-mcp-pro/
├── server.py              # MCP server (pool / transactions / export / audit)
├── pyproject.toml         # package metadata; entry: mysql-mcp-pro
├── .env.example
├── LICENSE                # MIT
├── README.md / README.en.md
└── scripts/
    ├── setup_test_db.py   # create the mcp_test sample database
    ├── test_v2.py         # 53-case regression test
    └── diag.py            # MySQL handshake diagnostics
```

## 🧪 Test Locally

```bash
# 1. Create the sample database (mcp_test: users + orders with foreign key)
python scripts/setup_test_db.py

# 2. Run the full regression suite
python scripts/test_v2.py
# → 53 passed, 0 failed
```

## 🤝 Contributing

Issues and PRs are welcome! Please:
1. Add tests for new features
2. Make sure all existing tests pass
3. Follow the existing code style

## 📄 License

[MIT](LICENSE)
