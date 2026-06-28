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

    # v1.3.26: 站点类型列（php / static），default 'php'（老站点全为 php）
    # 先检查列是否存在，不存在才加（幂等）
    cur.execute("PRAGMA table_info(sites)")
    cols = {row[1] for row in cur.fetchall()}
    if 'site_type' not in cols:
        try:
            cur.execute("ALTER TABLE sites ADD COLUMN site_type TEXT DEFAULT 'php'")
        except Exception:
            pass

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

    # v1.3.10+ 软件市场表
    cur.execute('''
    CREATE TABLE IF NOT EXISTS software (
        name TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        category TEXT NOT NULL,
        installed INTEGER DEFAULT 0,
        version TEXT,
        last_check DATETIME,
        last_install DATETIME
    )''')

    # v1.3.10+ 任务表（用于实时进度）
    cur.execute('''
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        target TEXT,
        status TEXT DEFAULT 'running',
        log TEXT DEFAULT '',
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        finished_at DATETIME,
        exit_code INTEGER
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