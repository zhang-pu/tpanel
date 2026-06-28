"""
TPanel - T面板 配置模块
"""
import os
import json

BASE_DIR = '/opt/tpanel'
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
SITES_DIR = os.path.join(BASE_DIR, 'sites')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
SSL_DIR = os.path.join(BASE_DIR, 'ssl')
CONFIG_DIR = os.path.join(BASE_DIR, 'config')
NGINX_CONF_DIR = '/etc/nginx/tpanel'

DB_PATH = os.path.join(DATA_DIR, 'tpanel.db')

# Nginx 配置目录（由 install.sh 创建）
os.makedirs(NGINX_CONF_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SITES_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(SSL_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

def load_config():
    path = os.path.join(CONFIG_DIR, 'tpanel.conf')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {
        'panel_port': 8848,
        'panel_domain': '',
        'php_versions': ['7.4', '8.0', '8.1', '8.2'],
        'default_php': '8.1',
        'auto_ssl_renew': True,
        'backup_retention_days': 7,
        'security_auto_update': True,
        'firewall_enabled': True,
        'ssh_port': 22,
    }

def save_config(cfg):
    path = os.path.join(CONFIG_DIR, 'tpanel.conf')
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)

def get_setting(key, default=''):
    """从数据库读取设置"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key, value):
    """v1.3.43+: busy_timeout + WAL 防锁"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                 (key, value, value))
    conn.commit()
    conn.close()

def get_panel_domain():
    """获取面板绑定的域名，无绑定则返回空字符串"""
    return get_setting('panel_domain', '')

def is_domain_allowed(host):
    """检查请求的 Host 是否在允许的域名列表中"""
    allowed = get_panel_domain().strip()
    if not allowed:
        return True  # 未绑定域名，不限制

    allowed = allowed.lower().strip()
    host = host.lower().strip()

    # 支持带端口的 host（如 localhost:8848）
    host_clean = host.split(':')[0]
    allowed_clean = allowed.split(':')[0]

    # 也允许 localhost 和 127.0.0.1
    safe_hosts = ['localhost', '127.0.0.1', '::1']
    if host_clean in safe_hosts:
        return True

    return host_clean == allowed_clean or host == allowed
# v1.3.34+: 用于 phpMyAdmin 自动登录 token 签名
_SECRET_FILE = os.path.join(DATA_DIR, ".secret_key")
def get_secret_key():
    """加载或生成 SECRET_KEY（启动时一次，进程内复用）"""
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "r") as f:
            return f.read().strip()
    sk = os.urandom(32).hex()
    with open(_SECRET_FILE, "w") as f:
        f.write(sk)
    try:
        os.chmod(_SECRET_FILE, 0o600)
        import pwd
        uid = pwd.getpwnam("tpanel").pw_uid
        gid = pwd.getpwnam("tpanel").pw_gid
        os.chown(_SECRET_FILE, uid, gid)
    except Exception:
        pass
    return sk

SECRET_KEY = get_secret_key()
