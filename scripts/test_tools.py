"""
端到端验证：直接导入 server 模块，调用 MCP 工具函数（绕过 stdio 传输层）。
这样既能验证真实工具逻辑，又不会卡在 stdio 协议协商上。
"""
import os, sys, json

# 注入环境变量
os.environ.update({
    'MYSQL_HOST': 'localhost',
    'MYSQL_PORT': '3306',
    'MYSQL_USER': 'root',
    'MYSQL_PASSWORD': '${MYSQL_PASSWORD}',
    'MYSQL_DATABASE': 'mcp_test',
    'MYSQL_CHARSET': 'utf8mb4',
    'READ_ONLY': 'false',
    'MAX_RESULT_ROWS': '1000',
    'QUERY_TIMEOUT': '30',
    'ALLOW_DDL': 'false',
})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

def show(title, result, maxlen=600):
    print(f'\n=== {title} ===')
    s = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(s[:maxlen] + ('...' if len(s) > maxlen else ''))

# 1. ping
show('1. ping', server.ping())

# 2. list_databases
show('2. list_databases', server.list_databases())

# 3. list_tables
show('3. list_tables', server.list_tables())

# 4. describe_table users
show('4. describe_table users', server.describe_table('users'), 1200)

# 5. execute_query JOIN
join_sql = (
    "SELECT u.name, u.city, COUNT(o.id) AS order_count, "
    "SUM(o.amount) AS total_spent "
    "FROM users u LEFT JOIN orders o ON u.id = o.user_id "
    "GROUP BY u.id ORDER BY total_spent DESC"
)
show('5. execute_query JOIN', server.execute_query(join_sql), 1000)

# 6. execute_select 参数化
show('6. execute_select (参数化)', server.execute_select(
    "SELECT id, name, email, balance FROM users WHERE balance > %s",
    params=[1000], limit=5))

# 7. get_table_data orders
show('7. get_table_data orders (已完成,按金额降序前3)', server.get_table_data(
    table='orders', where="status='completed'", order_by='amount DESC', limit=3))

# 8. count_rows
show('8. count_rows orders', server.count_rows('orders'))

# 9. INSERT + DELETE 事务回滚
show('9a. INSERT', server.execute_query(
    "INSERT INTO users (name, email, age, city, balance) VALUES (%s,%s,%s,%s,%s)",
    params=['测试用户','test@mcp.local',30,'测试城市',999.99]))
show('9b. DELETE', server.execute_query(
    "DELETE FROM users WHERE email = %s",
    params=['test@mcp.local']))

# 10. 安全拦截：DROP TABLE
show('10. 安全拦截 DROP TABLE', server.execute_query("DROP TABLE users"))

# 11. 安全拦截：GRANT
show('11. 安全拦截 GRANT', server.execute_query("GRANT ALL ON *.* TO 'hacker'@'%'"))

# 12. 只读专用工具拦截写操作
show('12. execute_select 拦截 INSERT', server.execute_select("DELETE FROM users"))

# 13. show_processlist
show('13. show_processlist', server.show_processlist(), 500)

# 14. get_server_status
show('14. get_server_status', server.get_server_status(), 500)

print('\n' + '='*60)
print('✅ 所有 MCP 工具端到端验证完毕')
