"""Shared database config for scripts. Reads credentials from environment variables.

Required environment variables (or .env file in repo root):
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""
import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except ImportError:
    pass


def get_conn_kwargs():
    """Return pymysql connection kwargs from environment."""
    return dict(
        host=os.getenv('MYSQL_HOST', 'localhost'),
        port=int(os.getenv('MYSQL_PORT', '3306')),
        user=os.getenv('MYSQL_USER', 'root'),
        password=os.getenv('MYSQL_PASSWORD', ''),
        charset=os.getenv('MYSQL_CHARSET', 'utf8mb4'),
        autocommit=True,
    )
