"""v2 全功能回归测试"""
import os, sys, json, glob

os.environ.update({
    'MYSQL_HOST':'localhost','MYSQL_PORT':'3306','MYSQL_USER':'root',
    'MYSQL_PASSWORD':'${MYSQL_PASSWORD}','MYSQL_DATABASE':'mcp_test','MYSQL_CHARSET':'utf8mb4',
    'READ_ONLY':'false','MAX_RESULT_ROWS':'1000','QUERY_TIMEOUT':'30','ALLOW_DDL':'false',
    'AUDIT_LOG':'true','POOL_SIZE':'3',
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 清理之前的导出
for f in glob.glob('exports/*'):
    os.remove(f)

import server

PASS, FAIL = 0, 0
def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'  ✅ {name}')
    else:
        FAIL += 1
        print(f'  ❌ {name}: {detail}')

def show(title):
    print(f'\n=== {title} ===')

# === 原有工具 ===
show('1. 原有基础工具（向后兼容）')
r = server.ping()
check('ping', r.get('status') == 'ok', r.get('error',''))
check('ping 有 poolStats', 'poolStats' in r)

r = server.list_databases()
check('list_databases', 'mcp_test' in r.get('databases', []))
check('list_databases userDatabases', len(r.get('userDatabases', [])) > 0)

r = server.list_tables()
check('list_tables', set(r.get('tables', [])) == {'users','orders'})
check('list_tables details', len(r.get('details', [])) == 2)

r = server.describe_table('users')
check('describe_table columns', len(r.get('columns', [])) == 7)
check('describe_table primaryKey', r.get('primaryKey') == ['id'])
check('describe_table indexes', len(r.get('indexes', [])) >= 2)
check('describe_table foreignKeys', 'createSQL' in r and 'foreignKeys' in r)

r = server.execute_query("SELECT COUNT(*) AS c FROM users")
check('execute_query SELECT', r.get('rows',[{}])[0].get('c') == 6, r)
r = server.execute_select("SELECT * FROM users WHERE balance > %s", params=[5000])
check('execute_select 参数化', r.get('rowCount') == 2)
r = server.get_table_data('orders', where="status='completed'", order_by='amount DESC', limit=3)
check('get_table_data', r.get('rowCount') == 3)
r = server.count_rows('orders')
check('count_rows', r.get('count') == 10)
r = server.show_processlist()
check('show_processlist', r.get('count',0) > 0)
r = server.get_server_status()
check('get_server_status', 'status' in r and 'Uptime' in r.get('status',{}))

# === 新增：元数据浏览 ===
show('2. 新增元数据浏览工具')
r = server.list_views()
check('list_views', 'views' in r and 'count' in r)
r = server.list_routines('PROCEDURE')
check('list_routines PROCEDURE', 'items' in r)
r = server.list_routines('FUNCTION')
check('list_routines FUNCTION', 'items' in r)
r = server.list_triggers()
check('list_triggers', 'triggers' in r)
r = server.er_graph()
check('er_graph nodes', len(r.get('nodes',[])) == 2)
check('er_graph edges', len(r.get('edges',[])) == 1)  # orders.user_id -> users.id
check('er_graph edge target', r.get('edges',[{}])[0].get('to_table') == 'users')

# === 新增：EXPLAIN / 导出 / 历史 ===
show('3. EXPLAIN 分析')
r = server.explain_query("SELECT * FROM orders WHERE user_id = 1")
check('explain_query explain', len(r.get('explain', [])) > 0)
check('explain_query insights', isinstance(r.get('insights'), list))
r = server.explain_query("SELECT * FROM orders WHERE user_id = 1", format='json')
check('explain_query JSON format', 'explain' in r)

show('4. 结果导出 CSV/JSON')
r = server.export_query("SELECT * FROM users", filename='users', format='csv')
check('export_csv', r.get('success') and r.get('rowCount') == 6 and os.path.exists(r.get('path','')))
print(f'    → {r.get("filename")} ({r.get("size")} bytes)')
r = server.export_query("SELECT * FROM orders", filename='orders', format='json')
check('export_json', r.get('success') and r.get('rowCount') == 10 and os.path.exists(r.get('path','')))
print(f'    → {r.get("filename")} ({r.get("size")} bytes)')

show('5. 查询历史')
r = server.list_query_history(limit=5)
check('query_history', r.get('count', 0) > 0)
check('query_history 最新一条ok', r.get('history', [{}])[-1].get('ok') is not None)

# === 新增：事务 ===
show('6. 事务支持')
r = server.begin_transaction()
check('begin_transaction', r.get('success') and r.get('sessionId'))
sid = r.get('sessionId')
if sid:
    # 事务内插入
    r = server.execute_query(
        "INSERT INTO users (name, email, age, city, balance) VALUES (%s,%s,%s,%s,%s)",
        params=['事务测试','tx@mcp.local',1,'北京',0], session_id=sid)
    check('事务内 INSERT', r.get('affectedRows') == 1, r)
    # 外部连接应看不到未提交数据
    r = server.execute_select("SELECT COUNT(*) AS c FROM users WHERE email='tx@mcp.local'")
    check('事务外看不到未提交数据', r.get('rows',[{}])[0].get('c') == 0)
    # 回滚
    r = server.rollback_transaction(sid)
    check('rollback', r.get('success'))
    r = server.execute_select("SELECT COUNT(*) AS c FROM users WHERE email='tx@mcp.local'")
    check('回滚后无数据', r.get('rows',[{}])[0].get('c') == 0)

    # commit 路径
    r = server.begin_transaction()
    sid2 = r.get('sessionId')
    r = server.execute_query(
        "INSERT INTO users (name, email, age, city, balance) VALUES (%s,%s,%s,%s,%s)",
        params=['提交测试','commit@mcp.local',2,'上海',100], session_id=sid2)
    r = server.commit_transaction(sid2)
    check('commit', r.get('success'))
    r = server.execute_select("SELECT COUNT(*) AS c FROM users WHERE email='commit@mcp.local'")
    check('提交后可见', r.get('rows',[{}])[0].get('c') == 1)
    # 清理
    server.execute_query("DELETE FROM users WHERE email IN ('tx@mcp.local','commit@mcp.local')")

# === 安全拦截 ===
show('7. 安全拦截（回归）')
check('拦截 DROP', 'error' in server.execute_query('DROP TABLE users'))
check('拦截 GRANT', 'error' in server.execute_query("GRANT ALL ON *.* TO 'h'@'%'"))
check('execute_select 拦截写', 'error' in server.execute_select('DELETE FROM users'))
check('export 拦截非SELECT', 'error' in server.export_query('DELETE FROM users','x'))
check('kill 在非只读模式下参数错误', 'error' in server.kill_query(999999999))

# === 连接池状态 ===
show('8. 连接池状态')
stats = server.POOL.stats()
print(f'    created={stats["created"]} reused={stats["reused"]} idle={stats["idle"]}')
check('连接池复用>0', stats['reused'] > 0)

# === 审计日志 ===
show('9. 审计日志落盘')
audit = server.EXPORT_DIR / 'audit.log'
check('audit.log 存在', audit.exists())
if audit.exists():
    with open(audit, encoding='utf-8') as f:
        lines = f.readlines()
    check(f'audit.log 有记录 ({len(lines)} 条)', len(lines) > 0)

# === 资源注册 ===
show('10. 资源注册验证')
r = server.resource_databases()
check('resource databases', 'mcp_test' in r)
r = server.resource_tables('mcp_test')
check('resource tables', 'users' in r and 'orders' in r)
r = server.resource_schema('mcp_test', 'users')
check('resource schema', 'id' in r and 'primaryKey' in r)
r = server.resource_er('mcp_test')
check('resource er', 'nodes' in r and 'edges' in r)

# === Prompts 注册 ===
show('11. Prompts 注册')
prompts = server.mcp._prompt_manager.list_prompts() if hasattr(server.mcp, '_prompt_manager') else []
prompt_names = [p.name for p in prompts]
for expected in ['generate_query','optimize_sql','compare_schema','database_summary']:
    check(f'prompt {expected}', expected in prompt_names)

print('\n' + '='*60)
print(f'测试结果: {PASS} 通过, {FAIL} 失败')
if FAIL == 0:
    print('🎉 全部通过！')
else:
    sys.exit(1)
