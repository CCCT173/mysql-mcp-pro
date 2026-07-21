"""Clean up test data after test runs. Reads credentials from env/.env."""
import os, sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except ImportError:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
r = server.execute_query("DELETE FROM users WHERE email = 'commit@mcp.local'")
print('清理测试数据:', r)
r = server.execute_query("SELECT COUNT(*) AS c FROM users")
print('当前 users 行数:', r['rows'][0]['c'])
server.POOL.close_all()
