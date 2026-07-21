"""Create the sample mcp_test database and insert seed data.

Reads credentials from environment variables (or .env in repo root):
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
import pymysql
from _db_config import get_conn_kwargs

conn = pymysql.connect(**get_conn_kwargs())
with conn.cursor() as cur:
    cur.execute('SELECT VERSION()')
    print('MySQL版本:', cur.fetchone()[0])

    cur.execute('CREATE DATABASE IF NOT EXISTS mcp_test DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci')
    cur.execute('USE mcp_test')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(50) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            age INT,
            city VARCHAR(50),
            balance DECIMAL(10,2) DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_city (city)
        ) ENGINE=InnoDB COMMENT='用户表'
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            product VARCHAR(100) NOT NULL,
            amount DECIMAL(10,2) NOT NULL,
            status ENUM('pending','paid','shipped','completed','cancelled') DEFAULT 'pending',
            order_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_user (user_id),
            INDEX idx_status (status),
            FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB COMMENT='订单表'
    ''')

    cur.execute('SELECT COUNT(*) FROM users')
    if cur.fetchone()[0] == 0:
        cur.executemany(
            'INSERT INTO users (name, email, age, city, balance) VALUES (%s,%s,%s,%s,%s)',
            [
                ('张三','zhangsan@example.com',28,'北京',1500.50),
                ('李四','lisi@example.com',35,'上海',8200.00),
                ('王五','wangwu@example.com',24,'广州',320.75),
                ('赵六','zhaoliu@example.com',42,'深圳',15000.00),
                ('孙七','sunqi@example.com',31,'北京',680.20),
                ('周八','zhouba@example.com',29,'杭州',4200.00),
            ],
        )
        cur.executemany(
            'INSERT INTO orders (user_id, product, amount, status) VALUES (%s,%s,%s,%s)',
            [
                (1,'机械键盘',399.00,'completed'),
                (1,'显示器支架',129.00,'shipped'),
                (2,'4K显示器',2999.00,'completed'),
                (2,'人体工学椅',1899.00,'paid'),
                (3,'鼠标垫',49.00,'completed'),
                (4,'MacBook Pro',14999.00,'completed'),
                (4,'AirPods Pro',1899.00,'shipped'),
                (5,'保温杯',89.00,'pending'),
                (6,'台灯',199.00,'completed'),
                (6,'插线板',59.00,'cancelled'),
            ],
        )
        print('已插入示例数据: 6 用户, 10 订单')
    else:
        print('示例数据已存在，跳过')

    cur.execute('SHOW TABLES')
    tables = [r[0] for r in cur.fetchall()]
    print('mcp_test 库的表:', tables)

conn.close()
print('OK - mcp_test 数据库准备完毕')
