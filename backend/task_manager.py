"""
TPanel - 任务管理器
用于软件安装、安全更新等长任务的执行 + 实时进度推送
"""
import sqlite3
import subprocess
import threading
import time
import os
import json
import re
import shutil
from datetime import datetime
from config import DB_PATH


def _detect_pkg_manager():
    """检测系统包管理器（apt/yum/dnf）"""
    for p in ['apt-get', 'yum', 'dnf']:
        if shutil.which(p):
            return p
    return None


def get_apt_cmd():
    """获取系统包管理器 + sudo"""
    pkg = _detect_pkg_manager()
    if pkg == 'apt-get':
        return ['sudo', 'apt-get', '-y']
    elif pkg == 'yum':
        return ['sudo', 'yum', '-y']
    elif pkg == 'dnf':
        return ['sudo', 'dnf', '-y']
    else:
        raise Exception('不支持的包管理器')





def _short_version(v):
    '''把 debian '7.0.33-89+0~20260514.116+debian12~1.gbpfef6bb' 短化成 '7.0.33'
    - 剥 epoch (4:)
    - 取 主版本号 (数字.数字.数字)
    - 失败返回原值
    '''
    if not v:
        return None
    v = re.sub(r"^\d+:", "", v)
    m = re.match(r"(\d+\.\d+\.\d+)", v)
    return m.group(1) if m else v

def init_software_table():
    """初始化软件列表（幂等）"""
    pkg = _detect_pkg_manager()
    is_deb = pkg == 'apt-get'

    # 软件白名单：name / 显示名 / 分类 / apt 包名（多个用逗号）
    catalog = [
        ('php5.6', 'PHP 5.6', 'PHP',
         'php5.6-fpm,php5.6-cli,php5.6-mysql,php5.6-curl,php5.6-mbstring,php5.6-xml,php5.6-zip,php5.6-gd'
         if is_deb else 'php56-php-fpm,php56-php-cli,php56-php-mysqlnd'),
        ('php7.0', 'PHP 7.0', 'PHP',
         'php7.0-fpm,php7.0-cli,php7.0-mysql,php7.0-curl,php7.0-mbstring,php7.0-xml,php7.0-zip,php7.0-gd'
         if is_deb else 'php70-php-fpm,php70-php-cli,php70-php-mysqlnd'),
        ('php7.4', 'PHP 7.4', 'PHP',
         'php7.4-fpm,php7.4-cli,php7.4-mysql,php7.4-curl,php7.4-mbstring,php7.4-xml,php7.4-zip,php7.4-gd'
         if is_deb else 'php74-php-fpm,php74-php-cli,php74-php-mysqlnd'),
        ('php8.0', 'PHP 8.0', 'PHP',
         'php8.0-fpm,php8.0-cli,php8.0-mysql,php8.0-curl,php8.0-mbstring,php8.0-xml,php8.0-zip,php8.0-gd'
         if is_deb else 'php80-php-fpm,php80-php-cli,php80-php-mysqlnd'),
        ('php8.1', 'PHP 8.1', 'PHP',
         'php8.1-fpm,php8.1-cli,php8.1-mysql,php8.1-curl,php8.1-mbstring,php8.1-xml,php8.1-zip,php8.1-gd'
         if is_deb else 'php81-php-fpm,php81-php-cli,php81-php-mysqlnd'),
        ('php8.2', 'PHP 8.2', 'PHP',
         'php8.2-fpm,php8.2-cli,php8.2-mysql,php8.2-curl,php8.2-mbstring,php8.2-xml,php8.2-zip,php8.2-gd'
         if is_deb else 'php82-php-fpm,php82-php-cli,php82-php-mysqlnd'),
        ('php8.3', 'PHP 8.3', 'PHP',
         'php8.3-fpm,php8.3-cli,php8.3-mysql,php8.3-curl,php8.3-mbstring,php8.3-xml,php8.3-zip,php8.3-gd'
         if is_deb else 'php83-php-fpm,php83-php-cli,php83-php-mysqlnd'),
        # v1.3.29: 补上 PHP 8.4（Sury 源已支持）
        ('php8.4', 'PHP 8.4', 'PHP',
         'php8.4-fpm,php8.4-cli,php8.4-mysql,php8.4-curl,php8.4-mbstring,php8.4-xml,php8.4-zip,php8.4-gd'
         if is_deb else 'php84-php-fpm,php84-php-cli,php84-php-mysqlnd'),
        ('phpmyadmin', 'phpMyAdmin', '数据库', 'phpmyadmin' if is_deb else 'phpMyAdmin'),
    ]

    conn = sqlite3.connect(DB_PATH)
    for name, display, cat, pkgs in catalog:
        # 探测实际安装状态
        installed = 0
        version = None
        first_pkg = pkgs.split(',')[0].split('/')[0]
        if is_deb:
            # v1.3.40.1: 加 timeout 防卡死（v1.3.38 计划中的保护，此处补齐）
            try:
                r = subprocess.run(['dpkg', '-s', first_pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode
            except subprocess.TimeoutExpired:
                r = 1  # 超时算未装，不阻塞列表
            if r == 0:
                installed = 1
                # 拿版本
                try:
                    v = subprocess.check_output(
                        ['dpkg-query', '-f=${Version}', '-W', first_pkg],
                        stderr=subprocess.DEVNULL, timeout=5
                    ).decode().strip()
                    version = _short_version(v) if v else None
                except Exception:
                    pass
        else:
            r = os.system(f'rpm -q {first_pkg} >/dev/null 2>&1')
            if r == 0:
                installed = 1
                try:
                    v = subprocess.check_output(
                        ['rpm', '-q', '--queryformat', '%{VERSION}', first_pkg],
                        stderr=subprocess.DEVNULL, timeout=5
                    ).decode().strip()
                    version = _short_version(v) if v else None
                except Exception:
                    pass

        # 已有则更新状态（不覆盖显示名等）
        row = conn.execute("SELECT name FROM software WHERE name = ?", (name,)).fetchone()
        if row:
            conn.execute("""UPDATE software SET installed = ?, version = ?, last_check = ? 
                            WHERE name = ?""",
                         (installed, version, datetime.now().isoformat(), name))
        else:
            conn.execute("""INSERT INTO software (name, display_name, category, installed, version, last_check) 
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (name, display, cat, installed, version, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def list_software(force_refresh=False):
    """列出所有软件 + 状态（v1.3.40.1 修复 force_refresh 参数未定义）"""
    # 注：force_refresh 参数当前未使用（保留接口），避免 TypeError 500
    init_software_table()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""SELECT name, display_name, category, installed, version, last_install 
                           FROM software ORDER BY category, name""").fetchall()
    conn.close()
    return [{
        'name': r[0], 'display_name': r[1], 'category': r[2],
        'installed': bool(r[3]), 'version': r[4], 'last_install': r[5]
    } for r in rows]


def get_software(name):
    """获取单个软件信息"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("""SELECT name, display_name, category, installed, version, last_install 
                          FROM software WHERE name = ?""", (name,)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        'name': row[0], 'display_name': row[1], 'category': row[2],
        'installed': bool(row[3]), 'version': row[4], 'last_install': row[5]
    }


def get_apt_packages(name):
    """从软件名反查 apt 包列表"""
    init_software_table()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT name FROM software WHERE name = ?", (name,)).fetchone()
    conn.close()
    if not row:
        return None
    # 直接从 catalog 重算（不存包名到 DB，因为跨系统不一样）
    pkg = _detect_pkg_manager()
    is_deb = pkg == 'apt-get'
    catalog = {
        'php5.6': 'php5.6-fpm,php5.6-cli,php5.6-mysql,php5.6-curl,php5.6-mbstring,php5.6-xml,php5.6-zip,php5.6-gd' if is_deb else 'php56-php-fpm,php56-php-cli',
        'php7.0': 'php7.0-fpm,php7.0-cli,php7.0-mysql,php7.0-curl,php7.0-mbstring,php7.0-xml,php7.0-zip,php7.0-gd' if is_deb else 'php70-php-fpm,php70-php-cli',
        'php7.4': 'php7.4-fpm,php7.4-cli,php7.4-mysql,php7.4-curl,php7.4-mbstring,php7.4-xml,php7.4-zip,php7.4-gd' if is_deb else 'php74-php-fpm,php74-php-cli',
        'php8.0': 'php8.0-fpm,php8.0-cli,php8.0-mysql,php8.0-curl,php8.0-mbstring,php8.0-xml,php8.0-zip,php8.0-gd' if is_deb else 'php80-php-fpm,php80-php-cli',
        'php8.1': 'php8.1-fpm,php8.1-cli,php8.1-mysql,php8.1-curl,php8.1-mbstring,php8.1-xml,php8.1-zip,php8.1-gd' if is_deb else 'php81-php-fpm,php81-php-cli',
        'php8.2': 'php8.2-fpm,php8.2-cli,php8.2-mysql,php8.2-curl,php8.2-mbstring,php8.2-xml,php8.2-zip,php8.2-gd' if is_deb else 'php82-php-fpm,php82-php-cli',
        'php8.3': 'php8.3-fpm,php8.3-cli,php8.3-mysql,php8.3-curl,php8.3-mbstring,php8.3-xml,php8.3-zip,php8.3-gd' if is_deb else 'php83-php-fpm,php83-php-cli',
        'php8.4': 'php8.4-fpm,php8.4-cli,php8.4-mysql,php8.4-curl,php8.4-mbstring,php8.4-xml,php8.4-zip,php8.4-gd' if is_deb else 'php84-php-fpm,php84-php-cli',
        'phpmyadmin': 'phpmyadmin' if is_deb else 'phpMyAdmin',
    }
    return catalog.get(name)


def setup_phpmyadmin_nginx(task_id=None):
    """phpMyAdmin 装完后自动配置 Nginx 8443 反代（v1.3.10 新增）

    写 /etc/nginx/sites-enabled/phpmyadmin.conf + nginx -t + reload
    失败时把错误追加到任务日志（如果有 task_id）
    """
    # 1. 找 phpMyAdmin 实际路径（Debian/Ubuntu 装完默认在这里）
    candidates = ['/usr/share/phpmyadmin', '/usr/share/phpmyadmin/htdocs']
    pma_dir = None
    for c in candidates:
        if os.path.isdir(c) and os.path.exists(os.path.join(c, 'index.php')):
            pma_dir = c
            break
    if not pma_dir:
        msg = 'setup_phpmyadmin_nginx: 找不到 phpMyAdmin 目录（/usr/share/phpmyadmin 不存在）'
        print(f'[TPanel] {msg}', flush=True)
        if task_id:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                         (f'\n\n{msg}', task_id))
            conn.commit()
            conn.close()
        return False

    # 2. 写 Nginx 配置文件
    conf = f"""# TPanel phpMyAdmin 反代配置（v1.3.10 自动写入）
# 管理命令：sudo nginx -t && sudo systemctl reload nginx
server {{
    listen 8443 default_server;
    listen [::]:8443 default_server;
    server_name _;

    root {pma_dir};
    index index.php index.html;

    access_log /var/log/nginx/phpmyadmin.access.log;
    error_log  /var/log/nginx/phpmyadmin.error.log;

    # 安全加固：屏蔽 phpMyAdmin 已知信息泄露路径
    location ~* /(libraries|setup/frames|sql) {{
        deny all;
        return 403;
    }}

    location / {{
        try_files $uri $uri/ /index.php?$args;
    }}

    location ~ \.php$ {{
        include fastcgi_params;
        fastcgi_pass 127.0.0.1:9000;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_read_timeout 300;
    }}
}}
"""
    conf_path = '/etc/nginx/sites-enabled/phpmyadmin.conf'
    try:
        # 写文件用 sudo（tpanel 用户没权限写 /etc/nginx）
        with open('/tmp/phpmyadmin.conf.tmp', 'w') as f:
            f.write(conf)
        r = subprocess.run(['sudo', 'mv', '/tmp/phpmyadmin.conf.tmp', conf_path],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise Exception(f'sudo mv 失败: {r.stderr.strip()}')
    except Exception as e:
        msg = f'setup_phpmyadmin_nginx: 写 {conf_path} 失败: {e}'
        print(f'[TPanel] {msg}', flush=True)
        if task_id:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                         (f'\n\n{msg}', task_id))
            conn.commit()
            conn.close()
        return False

    # 3. nginx -t 验证
    r = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        msg = f'setup_phpmyadmin_nginx: nginx -t 失败:\n{r.stderr.strip()}'
        print(f'[TPanel] {msg}', flush=True)
        if task_id:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                         (f'\n\n{msg}', task_id))
            conn.commit()
            conn.close()
        return False

    # 4. reload nginx
    r = subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        # reload 失败就 try restart
        r2 = subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'],
                            capture_output=True, text=True, timeout=10)
        if r2.returncode != 0:
            msg = f'setup_phpmyadmin_nginx: nginx reload/restart 失败: {r2.stderr.strip()}'
            print(f'[TPanel] {msg}', flush=True)
            if task_id:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                             (f'\n\n{msg}', task_id))
                conn.commit()
                conn.close()
            return False

    # 5. 确认 8443 端口没被占
    r = subprocess.run(['sudo', 'ss', '-tlnp'], capture_output=True, text=True, timeout=5)
    if ':8443' not in r.stdout:
        msg = 'setup_phpmyadmin_nginx: 警告 - 8443 端口没在监听'
        print(f'[TPanel] {msg}', flush=True)
        # 不算失败，配置已写入

    success_msg = f'setup_phpmyadmin_nginx: 成功 - {conf_path} 已写入，nginx 已 reload'
    print(f'[TPanel] {success_msg}', flush=True)
    if task_id:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                     (f'\n\n{success_msg}', task_id))
        conn.commit()
        conn.close()
    return True


def create_task(task_type, target, cmd, on_complete=None):
    """创建任务 + 启动后台进程

    on_complete（v1.3.10 新增）：可选回调函数，签名 on_complete(task_id, status)
    在任务结束（success/failed）后、software 表更新后调用。
    用于实现"装完 X 自动配 Y"这种联动。
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("INSERT INTO tasks (type, target, status) VALUES (?, ?, 'running')",
                       (task_type, target))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()

    def _run():
        try:
            proc = subprocess.Popen(
                cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            log_buffer = []
            for line in iter(proc.stdout.readline, ''):
                line = line.rstrip()
                log_buffer.append(line)
                # 写最新 200 行到 DB
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE tasks SET log = ? WHERE id = ?",
                             ('\n'.join(log_buffer[-200:]), task_id))
                conn.commit()
                conn.close()
            proc.wait()
            status = 'success' if proc.returncode == 0 else 'failed'
        except Exception as e:
            status = 'failed'
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                         (f'\n\nERROR: {e}', task_id))
            conn.commit()
            conn.close()
            # on_complete 也要在异常路径上调用（status='failed'）
            if on_complete:
                try:
                    on_complete(task_id, 'failed')
                except Exception as e2:
                    print(f'[TPanel] on_complete 异常: {e2}', flush=True)
            return

        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tasks SET status = ?, exit_code = ?, finished_at = ? WHERE id = ?",
                     (status, proc.returncode, datetime.now().isoformat(), task_id))
        conn.commit()
        conn.close()
        # 安装成功：更新 software 表
        if status == 'success' and task_type == 'software_install':
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE software SET installed = 1, last_install = ? WHERE name = ?",
                         (datetime.now().isoformat(), target))
            conn.commit()
            conn.close()

        # on_complete 钩子（v1.3.10）：success/failed 后都调，让钩子自己判断
        if on_complete:
            try:
                on_complete(task_id, status)
            except Exception as e:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE tasks SET log = log || ? WHERE id = ?",
                             (f'\n\non_complete 异常: {e}', task_id))
                conn.commit()
                conn.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return task_id


def get_task(task_id):
    """获取任务状态 + 日志"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("""SELECT id, type, target, status, log, started_at, finished_at, exit_code 
                          FROM tasks WHERE id = ?""", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        'id': row[0], 'type': row[1], 'target': row[2], 'status': row[3],
        'log': row[4] or '', 'started_at': row[5], 'finished_at': row[6],
        'exit_code': row[7]
    }


def get_running_task_by_type(task_type, target=None):
    """获取正在运行的同类型任务（防并发）"""
    conn = sqlite3.connect(DB_PATH)
    if target is not None:
        row = conn.execute("""SELECT id FROM tasks 
                              WHERE type = ? AND target = ? AND status = 'running'""",
                           (task_type, target)).fetchone()
    else:
        row = conn.execute("""SELECT id FROM tasks 
                              WHERE type = ? AND status = 'running'""",
                           (task_type,)).fetchone()
    conn.close()
    return row[0] if row else None


def cleanup_old_tasks(days=7):
    """清理 N 天前的已完成任务"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""DELETE FROM tasks 
                    WHERE status != 'running' 
                    AND finished_at < datetime('now', ?)""",
                 (f'-{days} days',))
    conn.commit()
    conn.close()
