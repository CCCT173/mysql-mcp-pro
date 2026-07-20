"""
端到端验证：通过 MCP stdio 协议调用 server.py 的每个工具
"""
import sys, os, json, subprocess, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, 'server.py')
PY = 'C:/Users/CT/.workbuddy/binaries/python/envs/default/Scripts/python.exe'

# 通过环境变量注入配置（模拟 WorkBuddy 启动方式）
env = os.environ.copy()
env.update({
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
    'PYTHONIOENCODING': 'utf-8',
})

proc = subprocess.Popen(
    [PY, SERVER],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env=env, bufsize=0,
)

_req_id = 0
def send(method, params=None):
    global _req_id
    _req_id += 1
    msg = {"jsonrpc":"2.0","id":_req_id,"method":method}
    if params is not None:
        msg["params"] = params
    data = json.dumps(msg).encode('utf-8')
    header = f"Content-Length: {len(data)}\r\n\r\n".encode('ascii')
    proc.stdin.write(header + data)
    proc.stdin.flush()
    return _req_id

def recv(timeout=15):
    proc.stdout.timeout = timeout
    # 读取 header
    header = b''
    while b'\r\n\r\n' not in header:
        ch = proc.stdout.read(1)
        if not ch:
            raise RuntimeError('server closed stdout')
        header += ch
    length = 0
    for line in header.decode('ascii').split('\r\n'):
        if line.lower().startswith('content-length:'):
            length = int(line.split(':',1)[1].strip())
    body = proc.stdout.read(length)
    return json.loads(body.decode('utf-8'))

def call_tool(name, arguments=None):
    rid = send("tools/call", {"name": name, "arguments": arguments or {}})
    while True:
        msg = recv()
        if msg.get("id") == rid:
            return msg

print('=== 1. 初始化 initialize ===')
send("initialize", {
    "protocolVersion":"2024-11-05",
    "capabilities":{},
    "clientInfo":{"name":"test","version":"1.0"}
})
print(json.dumps(recv(), ensure_ascii=False, indent=2)[:400])
send("notifications/initialized")

print('\n=== 2. list_tools ===')
send("tools/list")
r = recv()
tools = [t["name"] for t in r["result"]["tools"]]
print(f'共 {len(tools)} 个工具:', tools)

print('\n=== 3. ping ===')
r = call_tool("ping")
print(json.dumps(r, ensure_ascii=False, indent=2)[:600])

print('\n=== 4. list_databases ===')
r = call_tool("list_databases")
print(json.dumps(r, ensure_ascii=False, indent=2)[:400])

print('\n=== 5. list_tables ===')
r = call_tool("list_tables")
print(json.dumps(r, ensure_ascii=False, indent=2)[:800])

print('\n=== 6. describe_table users ===')
r = call_tool("describe_table", {"table":"users"})
out = json.dumps(r, ensure_ascii=False)
print(out[:800])

print('\n=== 7. execute_query SELECT ===')
r = call_tool("execute_query", {
    "sql": "SELECT u.name, u.city, COUNT(o.id) AS order_count, SUM(o.amount) AS total_spent "
           "FROM users u LEFT JOIN orders o ON u.id = o.user_id "
           "GROUP BY u.id ORDER BY total_spent DESC"
})
print(json.dumps(r, ensure_ascii=False, indent=2)[:1200])

print('\n=== 8. execute_select with LIMIT ===')
r = call_tool("execute_select", {
    "sql": "SELECT id, name, email, balance FROM users WHERE balance > %s",
    "params": [1000],
    "limit": 5
})
print(json.dumps(r, ensure_ascii=False, indent=2)[:800])

print('\n=== 9. get_table_data orders ===')
r = call_tool("get_table_data", {
    "table":"orders",
    "where":"status='completed'",
    "order_by":"amount DESC",
    "limit":3
})
print(json.dumps(r, ensure_ascii=False, indent=2)[:800])

print('\n=== 10. count_rows ===')
r = call_tool("count_rows", {"table":"orders"})
print(json.dumps(r, ensure_ascii=False, indent=2))

print('\n=== 11. INSERT 写操作测试 ===')
r = call_tool("execute_query", {
    "sql": "INSERT INTO users (name, email, age, city, balance) VALUES (%s,%s,%s,%s,%s)",
    "params": ["测试用户","test@mcp.local",30,"测试城市",999.99]
})
print(json.dumps(r, ensure_ascii=False, indent=2))

print('\n=== 12. 验证插入后回滚（删除刚才的测试数据）===')
r = call_tool("execute_query", {
    "sql": "DELETE FROM users WHERE email = %s",
    "params": ["test@mcp.local"]
})
print(json.dumps(r, ensure_ascii=False, indent=2))

print('\n=== 13. 安全拦截测试 - 危险语句 ===')
r = call_tool("execute_query", {"sql":"DROP TABLE users"})
print(json.dumps(r, ensure_ascii=False, indent=2))

print('\n=== 14. show_processlist ===')
r = call_tool("show_processlist")
txt = json.dumps(r, ensure_ascii=False)
print(txt[:500])

print('\n=== 15. get_server_status ===')
r = call_tool("get_server_status")
txt = json.dumps(r, ensure_ascii=False)
print(txt[:500])

proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()

print('\n✅ 所有 MCP 工具端到端验证完毕')
