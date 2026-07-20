import os, sys
os.environ.update({
    'MYSQL_HOST':'localhost','MYSQL_PORT':'3306','MYSQL_USER':'root',
    'MYSQL_PASSWORD':'${MYSQL_PASSWORD}','MYSQL_DATABASE':'mcp_test','MYSQL_CHARSET':'utf8mb4',
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
r = server.execute_query("DELETE FROM users WHERE email = 'commit@mcp.local'")
print('清理测试数据:', r)
r = server.execute_query("SELECT COUNT(*) AS c FROM users")
print('当前 users 行数:', r['rows'][0]['c'])
server.POOL.close_all()
