"""
TPanel - 数据库初始化
"""
import sqlite3
import os
import bcrypt
from config import DB_PATH, BASE_DIR

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
    CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        domain TEXT NOT NULL UNIQUE,
        site_user TEXT NOT NULL UNIQUE,
        site_path TEXT NOT NULL,
        php_version TEXT DEFAULT '8.1',
        status TEXT DEFAULT 'running',
        ssl_enabled INTEGER DEFAULT 0,
        ssl_cert_path TEXT,
        ssl_key_path TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS databases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        name TEXT NOT NULL UNIQUE,
        db_user TEXT NOT NULL UNIQUE,
        db_pass TEXT NOT NULL,
        charset TEXT DEFAULT 'utf8mb4',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        type TEXT DEFAULT 'local',
        file_path TEXT,
        size INTEGER,
        status TEXT DEFAULT 'success',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS ssl_certs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        domain TEXT NOT NULL,
        cert_path TEXT NOT NULL,
        key_path TEXT NOT NULL,
        expire_date TEXT,
        auto_renew INTEGER DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS cron_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        schedule TEXT NOT NULL,
        command TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_run DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS security_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        details TEXT,
        ip TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')

    cur.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    # 默认管理员账号 admin / tpanel.cn
    cur.execute("SELECT id FROM admin WHERE username = ?", ('admin',))
    if not cur.fetchone():
        pw_hash = bcrypt.hashpw(b'tpanel.cn', bcrypt.gensalt()).decode()
        cur.execute("INSERT INTO admin (username, password_hash) VALUES (?, ?)",
                    ('admin', pw_hash))
        conn.commit()

    conn.close()
    print("[TPanel] 数据库初始化完成")

if __name__ == '__main__':
    init_db()