# mysql-mcp-pro

> 🇺🇸 [English version available here](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green)](https://modelcontextprotocol.io)

一个生产级的 **MySQL MCP Server**，让 AI 助手（Claude Desktop / WorkBuddy / Cursor / Cline 等）通过标准化协议安全高效地操作 MySQL 数据库。

## ✨ 为什么选 mysql-mcp-pro？

相比社区其他 MySQL MCP 实现，本项目提供了更完整的能力集：

| 特性 | mysql-mcp-pro | 其他 MCP |
|------|:---:|:---:|
| 连接池（减少握手开销） | ✅ | ❌ |
| 事务支持（begin/commit/rollback） | ✅ | ❌ |
| EXPLAIN 自动解读 | ✅ | ❌ |
| 结果导出 CSV/JSON（Excel 友好 BOM） | ✅ | ❌ |
| ER 关系图数据（nodes/edges） | ✅ | 少数 |
| 视图 / 存储过程 / 函数 / 触发器 浏览 | ✅ | 少数 |
| 查询历史 & 审计日志 | ✅ | ❌ |
| MCP Prompts 模板（生成/优化/对比/概览） | ✅ | ❌ |
| 安全分级（只读/DDL/危险语句/超时/行限/参数化） | ✅ | 基础 |
| 多传输模式（stdio / SSE / streamable-http） | ✅ | 部分 |

## 🚀 快速开始

### 1. 安装

```bash
# 从源码（推荐先 clone 再使用）
git clone https://github.com/CCCT173/mysql-mcp-pro.git
cd mysql-mcp-pro
pip install -e .

# 或从 PyPI（待发布）
pip install mysql-mcp-pro
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并编辑：

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=your_database

READ_ONLY=false         # 只读模式
ALLOW_DDL=false          # 是否允许 CREATE/ALTER/DROP
MAX_RESULT_ROWS=1000     # 单次查询最大返回行数
QUERY_TIMEOUT=30         # 查询超时(秒)

POOL_SIZE=5
AUDIT_LOG=true           # 审计日志
EXPORT_DIR=./exports     # 导出目录
```

### 3. 在你的 MCP 客户端注册

#### WorkBuddy

编辑 `~/.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "mysql": {
      "command": "python",
      "args": ["-m", "server"],
      "cwd": "D:/path/to/mysql-mcp-pro",
      "env": {
        "MYSQL_HOST": "localhost",
        "MYSQL_PORT": "3306",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "your_password",
        "MYSQL_DATABASE": "your_database"
      }
    }
  }
}
```

或者如果你已经 `pip install -e .`：

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
        "MYSQL_DATABASE": "your_database"
      }
    }
  }
}
```

#### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或 `%APPDATA%/Claude/claude_desktop_config.json`（Windows）：

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

#### Cursor / Cline

在项目根目录创建 `.cursor/mcp.json`（Cursor）或在 Cline 设置中添加，格式同上。

## Tools

> Tools are automatically extracted by MCP registries (Smithery, mcp.so, glama, etc.).

- **ping** — Test MySQL connection; returns server version and connection-pool stats.
- **get_server_status** — Key MySQL status metrics (connections, QPS, slow queries, InnoDB rows, traffic).
- **list_databases** — List all databases (system vs user).
- **list_tables** — List tables with engine, row count, size, comment. Optional `database` argument.
- **describe_table** — Show table schema: columns, primary key, indexes, foreign keys, CREATE SQL. Arguments: `table` (required), `database` (optional).
- **list_views** — List views in the database.
- **list_routines** — List stored procedures or functions. Arguments: `routine_type` (`PROCEDURE`/`FUNCTION`), `database`.
- **list_triggers** — List triggers in the database.
- **er_graph** — Cross-table foreign-key graph as nodes + edges, ready for ER diagrams.
- **execute_query** — Execute any SQL (read/write/DDL, safety-guarded), parameterized. Arguments: `sql`, `database`, `params`, `session_id`.
- **execute_select** — Read-only SELECT/SHOW/DESCRIBE/EXPLAIN with extra safety layer. Arguments: `sql`, `database`, `params`, `limit`.
- **get_table_data** — Quick paginated table fetch. Arguments: `table`, `columns`, `where`, `order_by`, `limit`, `offset`.
- **count_rows** — Count rows in a table with optional WHERE. Arguments: `table`, `database`, `where`.
- **explain_query** — Run `EXPLAIN` on a query and return plain-language performance insights. Arguments: `sql`, `database`, `params`, `format`.
- **export_query** — Export SELECT results to a CSV (UTF-8 BOM, Excel-friendly) or JSON file. Arguments: `sql`, `filename`, `format` (`csv`/`json`), `database`, `params`.
- **list_query_history** — Recent SQL executed in this session (duration, row count, errors). Argument: `limit`.
- **begin_transaction** — Start a new transaction; returns a `session_id` for chained calls. Argument: `database`.
- **commit_transaction** — Commit an open transaction. Argument: `session_id`.
- **rollback_transaction** — Rollback an open transaction. Argument: `session_id`.
- **show_processlist** — Show the current MySQL process list (`SHOW FULL PROCESSLIST`).
- **kill_query** — Kill a MySQL process (requires SUPER privilege). Argument: `process_id`.

## 🛠 工具一览（25 个）

### 连接 & 状态

- **`ping`** — 测试连接、返回服务器版本与连接池状态
- **`get_server_status`** — MySQL 关键指标（连接数、QPS、慢查询、流量等）

### 元数据浏览

- **`list_databases`** — 列出所有数据库（区分系统库 / 用户库）
- **`list_tables`** — 列出表，含引擎、行数、大小、注释
- **`describe_table`** — 表结构：字段、主键、索引、外键、建表 SQL
- **`list_views`** — 列出视图
- **`list_routines`** — 列出存储过程 / 函数
- **`list_triggers`** — 列出触发器
- **`er_graph`** — 跨表外键关系（nodes + edges），便于画 ER 图

### 查询执行

- **`execute_query`** — 执行任意 SQL（读/写/DDL，受安全策略约束），支持参数化
- **`execute_select`** — 只读 SELECT 专用（即使配置错误也只允许读）
- **`get_table_data`** — 快速取数：columns / where / order_by / limit / offset
- **`count_rows`** — 统计行数（支持可选 WHERE）

### 分析 & 导出

- **`explain_query`** — `EXPLAIN` 执行计划 + 中文性能洞察
- **`export_query`** — 结果导出 CSV（UTF-8 BOM，Excel 友好）或 JSON 文件
- **`list_query_history`** — 本会话 SQL 历史（耗时 / 行数 / 错误）

### 事务

- **`begin_transaction`** — 开启事务，返回 session_id
- **`commit_transaction`** — 提交事务
- **`rollback_transaction`** — 回滚事务

### 监控 & 管理

- **`show_processlist`** — 当前 MySQL 进程列表
- **`kill_query`** — Kill 指定进程（需要 SUPER 权限）

### MCP Resources（4 个）

- `mysql://databases` — 数据库列表
- `mysql://tables/{database}` — 表列表
- `mysql://schema/{database}/{table}` — 表结构
- `mysql://er/{database}` — ER 关系图数据

### MCP Prompts（4 个模板）

- **`generate_query`** — 根据自然语言和表信息生成 SQL
- **`optimize_sql`** — 分析并优化现有 SQL 性能
- **`compare_schema`** — 对比两张表的结构差异
- **`database_summary`** — 数据库整体概览报告

## 🔒 安全模型

```
危险语句（GRANT/REVOKE/SHUTDOWN/KILL/FLUSH/RESET）→ 永远禁止
DDL（CREATE/ALTER/DROP/RENAME/TRUNCATE）            → 由 ALLOW_DDL 控制
写操作（INSERT/UPDATE/DELETE）                       → 由 READ_ONLY 控制
读操作（SELECT/SHOW/DESCRIBE/EXPLAIN）               → 永远允许
```

- 参数化查询 `%s` 占位符，杜绝 SQL 注入
- 结果集默认 1000 行上限，防止意外返回海量数据
- 服务端 `MAX_EXECUTION_TIME` 强制查询超时
- 所有执行 SQL 落盘到 `exports/audit.log`

## 📁 项目结构

```
mysql-mcp-pro/
├── server.py              # MCP 服务器（连接池 / 事务 / 导出 / 审计）
├── pyproject.toml         # 包元数据，入口 mysql-mcp-pro
├── .env.example           # 环境变量模板
├── LICENSE                # MIT
├── README.md / README.en.md
└── scripts/
    ├── setup_test_db.py   # 创建 mcp_test 样例数据库
    ├── test_v2.py         # 53 项回归测试
    └── diag.py            # MySQL 握手包诊断工具
```

## 🧪 本地测试

```bash
# 1. 创建样例数据库（mcp_test，users + orders 表，带外键）
python scripts/setup_test_db.py

# 2. 跑全量回归测试
python scripts/test_v2.py
# → 53 passed, 0 failed
```

## 🤝 贡献

欢迎提 issue 和 PR！请确保：
1. 新功能带测试用例
2. 现有测试全部通过
3. 遵循现有代码风格

## 📄 License

[MIT](LICENSE)
