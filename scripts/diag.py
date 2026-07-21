"""诊断：3306 端口上到底是什么，尝试多种客户端和连接方式"""
import socket, sys, struct

HOST, PORT = '127.0.0.1', 3306

# 1. 读取握手包（不做认证，看服务端宣告的版本和认证插件）
s = socket.socket()
s.settimeout(5)
try:
    s.connect((HOST, PORT))
except Exception as e:
    print('端口不可达:', e)
    sys.exit(1)

# MySQL 握手包：3字节长度 + 1字节序号 + payload
def recv_packet(sock):
    hdr = sock.recv(4)
    if len(hdr) < 4:
        return None
    length = hdr[0] | (hdr[1] << 8) | (hdr[2] << 16)
    seq = hdr[3]
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    return seq, data

pkt = recv_packet(s)
if not pkt:
    print('未收到握手包')
    sys.exit(1)
seq, payload = pkt
print(f'握手包长度={len(payload)}')

# 解析握手包 v10
proto = payload[0]
payload = payload[1:]
null = payload.index(b'\x00')
server_ver = payload[:null].decode('utf-8', errors='replace')
payload = payload[null+1:]
conn_id = struct.unpack('<I', payload[:4])[0]
payload = payload[4:]
auth_data_1 = payload[:8]
payload = payload[8:]
filler = payload[0]
payload = payload[1:]
cap_low = struct.unpack('<H', payload[:2])[0]
payload = payload[2:]
charset = payload[0] if payload else 0
payload = payload[1:]
status = struct.unpack('<H', payload[:2])[0] if len(payload) >= 2 else 0
payload = payload[2:]
cap_high = struct.unpack('<H', payload[:2])[0] if len(payload) >= 2 else 0
capabilities = cap_low | (cap_high << 16)
payload = payload[2:]
auth_len = payload[0] if payload else 0
payload = payload[1:]
reserved = payload[:10]
payload = payload[10:]

auth_data_2 = b''
auth_plugin_name = ''
if capabilities & 0x00080000:  # CLIENT_PLUGIN_AUTH
    auth_len_2 = max(13, auth_len - 8)
    auth_data_2 = payload[:auth_len_2]
    payload = payload[auth_len_2:]
    null = payload.find(b'\x00')
    if null >= 0:
        auth_plugin_name = payload[:null].decode('utf-8', errors='replace')

salt = (auth_data_1 + auth_data_2).rstrip(b'\x00')
print(f'协议版本: {proto}')
print(f'服务器版本: {server_ver}')
print(f'连接ID: {conn_id}')
print(f'字符集: {charset}')
print(f'状态: {status:#06x}')
print(f'认证插件: {auth_plugin_name or "(未声明)"}')
print(f'Salt长度: {len(salt)}')
print(f'Capability flags: {capabilities:#010x}')
s.close()

# 2. 用 mysql.connector 再试（官方驱动，auth_plugin 支持最全）
print('\n--- mysql.connector 尝试 ---')
try:
    import mysql.connector
    import os
    test_pw = os.getenv('MYSQL_PASSWORD', '')
    for plugin in [None, 'mysql_native_password', 'caching_sha2_password', 'mysql_clear_password']:
        for pw in ([test_pw] if test_pw else []):
            try:
                kw = dict(host=HOST, port=PORT, user=os.getenv('MYSQL_USER','root'), password=pw,
                          connection_timeout=5, autocommit=True)
                if plugin:
                    kw['auth_plugin'] = plugin
                c = mysql.connector.connect(**kw)
                cur = c.cursor()
                cur.execute('SELECT VERSION(), USER(), @@ssl_cipher')
                print(f'OK plugin={plugin} pw=<set>: {cur.fetchone()}')
                c.close()
                sys.exit(0)
            except Exception as e:
                print(f'FAIL plugin={plugin}: {e}')
except ImportError:
    print('mysql.connector 未安装')

# 3. 检查端口上是不是 MariaDB / 其他服务 banner
print('\n--- 端口 banner 已在上方握手包中 ---')
