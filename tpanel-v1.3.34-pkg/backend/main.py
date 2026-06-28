"""
TPanel - T面板 主程序
"""
import os
import sys
import secrets
import bcrypt
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, session, redirect, Response
from flask_cors import CORS

# 导入各模块
from config import DB_PATH, load_config, save_config, set_setting, get_setting, get_panel_domain, SECRET_KEY
from system import (
    nginx_status, nginx_reload, mysql_status,
    create_site_user, delete_site_user,
    write_nginx_config, remove_nginx_config,
    create_mysql_db, delete_mysql_db,
    backup_site, restore_backup,
    run_security_update, get_security_status, get_system_stats, write_log,
    setup_php_fpm_listen,  # v1.3.29
    change_db_password,  # v1.3.34
)
from file_manager import list_directory, read_file, write_file, upload_file, delete_file, chmod_file, create_directory
from ssl_manager import get_all_certs, apply_letsencrypt, renew_cert, renew_all_expiring, deploy_ssl, check_certs_status, _get_real_site_path
from cron_manager import get_all_crons, create_cron, delete_cron, enable_cron, run_cron_now, validate_cron_expression
from remote_backup import run_remote_backup, sync_restore, test_rsync_connection, get_backup_stats

app = Flask(__name__, static_folder='../frontend')
app.secret_key = secrets.token_hex(32)
CORS(app, supports_credentials=True)

# ==========================
# 域名绑定中间件
# ==========================

@app.before_request
def check_panel_domain():
    """如果面板绑定了域名，只允许该域名访问"""
    allowed = get_panel_domain().strip()
    import sys
    print(f'[DEBUG panel_domain] allowed={allowed!r} path={request.path!r} host={request.host!r}', file=sys.stderr, flush=True)
    if not allowed:
        return  # 未绑定，不限制

    host = request.host.lower()
    allowed_clean = allowed.lower().strip().split(':')[0]
    host_clean = host.split(':')[0]

    # v1.3.19+：安全白名单只放行 localhost 字面意思（开发用），所有 IP 走域名匹配
    if host_clean in ('localhost', '::1'):
        return
    # 127.0.0.1 也允许（用 IP 访问后台运维用）
    if host_clean == '127.0.0.1':
        return

    if host_clean != allowed_clean:
        return jsonify({'code': 403, 'msg': f'面板已绑定域名 {allowed}，请使用该域名访问'}), 403

# ==========================
# 装饰器
# ==========================

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # v1.3.27 修复：SSE 走 query string 传 token（EventSource 不能自定义 header）
        # 优先 header（常规 API），fallback ?token=（SSE）
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        if not token:
            token = request.args.get('token', '').strip()
        expected = get_setting('api_token', '')
        if not expected or token != expected:
            # 也检查 session
            if 'admin' not in session:
                return jsonify({'code': 401, 'msg': '未授权'}), 401
        return f(*args, **kwargs)
    return decorated

# ==========================
# 认证 API
# ==========================

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id, password_hash FROM admin WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({'code': 401, 'msg': '用户名或密码错误'})

    if bcrypt.checkpw(password.encode(), row[1].encode()):
        session['admin'] = True
        session['username'] = username

        # 生成 API token
        token = secrets.token_hex(32)
        set_setting('api_token', token)
        set_setting('last_login', datetime.now().isoformat())

        write_log('login', f'用户 {username} 登录成功', request.remote_addr)
        return jsonify({'code': 0, 'msg': '登录成功', 'token': token})

    write_log('login_fail', f'用户 {username} 登录失败', request.remote_addr)
    return jsonify({'code': 401, 'msg': '用户名或密码错误'})

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    username = session.get('username', 'unknown')
    session.clear()
    write_log('logout', f'用户 {username} 退出', request.remote_addr)
    return jsonify({'code': 0, 'msg': '已退出'})

@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def api_change_password():
    """v1.3.18 新增：改管理员密码"""
    data = request.json or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password:
        return jsonify({'code': 400, 'msg': '请提供旧密码和新密码'})

    if len(new_password) < 6:
        return jsonify({'code': 400, 'msg': '新密码至少 6 位'})

    username = session.get('username', 'admin')
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT password_hash FROM admin WHERE username = ?", (username,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'code': 404, 'msg': '用户不存在'})

    if not bcrypt.checkpw(old_password.encode(), row[0].encode()):
        conn.close()
        write_log('change_password_fail', f'用户 {username} 改密码失败（旧密码错）', request.remote_addr)
        return jsonify({'code': 401, 'msg': '旧密码错误'})

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn.execute("UPDATE admin SET password_hash = ? WHERE username = ?", (new_hash, username))
    conn.commit()
    conn.close()
    write_log('change_password', f'用户 {username} 改密码成功', request.remote_addr)
    return jsonify({'code': 0, 'msg': '密码已修改，请重新登录'})

@app.route('/api/auth/check', methods=['GET'])
def api_check():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token == get_setting('api_token', ''):
        return jsonify({'code': 0, 'msg': '有效', 'username': session.get('username', 'admin')})
    if 'admin' in session:
        return jsonify({'code': 0, 'msg': '有效', 'username': session.get('username', 'admin')})
    return jsonify({'code': 401, 'msg': '无效'}), 401

# ==========================
# 系统状态
# ==========================

@app.route('/api/system/stats', methods=['GET'])
@require_auth
def api_system_stats():
    stats = get_system_stats()
    security = get_security_status()
    stats.update(security)
    return jsonify({'code': 0, 'data': stats})

# ==========================
# 站点管理
# ==========================

@app.route('/api/sites', methods=['GET'])
@require_auth
def api_sites_list():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM sites ORDER BY id DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return jsonify({'code': 0, 'data': rows})

@app.route('/api/sites', methods=['POST'])
@require_auth
def api_sites_create():
    data = request.json or {}
    domain = data.get('domain', '').strip().lower()
    name = data.get('name', domain)
    site_type = data.get('type', 'php')  # v1.3.26 新增：'php' | 'static'
    php_version = data.get('php_version', '8.2')
    site_user = domain.replace('.', '_')

    if not domain:
        return jsonify({'code': 400, 'msg': '域名不能为空'})

    if site_type not in ('php', 'static'):
        return jsonify({'code': 400, 'msg': '站点类型必须是 php 或 static'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM sites WHERE domain = ?", (domain,))
    if cur.fetchone():
        conn.close()
        return jsonify({'code': 400, 'msg': '站点已存在'})

    site_path = f'/opt/tpanel/sites/{site_user}'

    # 1. 创建 Linux 用户
    ok, msg = create_site_user(site_user)
    if not ok:
        conn.close()
        return jsonify({'code': 500, 'msg': f'创建系统用户失败: {msg}'})

    # 2. 创建目录并写入默认首页
    os.makedirs(site_path, exist_ok=True)
    os.makedirs(f'{site_path}/public', exist_ok=True)
    if site_type == 'php':
        with open(f'{site_path}/public/index.php', 'w') as f:
            f.write(f'<?php\n// Site: {domain}\n// Managed by TPanel\nphpinfo();\n')
    else:
        # v1.3.26: 静态站点生成一个可看的 index.html（不是空页）
        static_html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{domain} - 站点已就绪</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
           background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
           min-height: 100vh; display: flex; align-items: center; justify-content: center;
           color: #e2e8f0; }}
    .card {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 16px; padding: 48px 56px; max-width: 600px;
            backdrop-filter: blur(10px); }}
    h1 {{ font-size: 32px; margin-bottom: 12px; color: #22c55e; }}
    p {{ line-height: 1.8; margin-bottom: 12px; }}
    code {{ background: rgba(255,255,255,0.1); padding: 2px 8px; border-radius: 4px;
            font-family: "SF Mono", monospace; font-size: 14px; color: #fbbf24; }}
    .file-list {{ background: rgba(0,0,0,0.3); border-radius: 8px; padding: 16px;
                  margin-top: 20px; font-family: monospace; font-size: 14px; }}
    .footer {{ margin-top: 24px; font-size: 13px; color: #94a3b8; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🌿 站点已就绪</h1>
    <p>域名: <code>{domain}</code></p>
    <p>类型: <code>静态页面</code>（不需要 PHP-FPM）</p>
    <p>管理: <code>TPanel 面板</code></p>
    <div class="file-list">
      📁 public/<br>
      &nbsp;&nbsp;📄 index.html  ← 你看到的这个页面
    </div>
    <p class="footer">
      上传你的 HTML / CSS / JS 文件到 <code>public/</code> 目录即可。<br>
      Powered by <a href="https://tpanel.cn" style="color:#22c55e;text-decoration:none">TPanel</a>
    </p>
  </div>
</body>
</html>
'''
        with open(f'{site_path}/public/index.html', 'w') as f:
            f.write(static_html)
    os.makedirs(f'{site_path}/logs', exist_ok=True)

    # 3. 写 Nginx 配置（v1.3.26 传 site_type 进去决定要不要 PHP-FPM 反代）
    ok, msg = write_nginx_config(domain, f'{site_path}/public', php_version, ssl=False, site_type=site_type)
    if not ok:
        conn.close()
        return jsonify({'code': 500, 'msg': f'Nginx 配置失败: {msg}'})

    # 4. 写入数据库
    cur.execute("""INSERT INTO sites (name, domain, site_user, site_path, php_version, status, site_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, domain, site_user, f'{site_path}/public', php_version, 'running', site_type))
    conn.commit()
    site_id = cur.lastrowid
    conn.close()

    write_log('site_create', f'创建站点 {domain}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '站点创建成功', 'data': {'id': site_id}})

@app.route('/api/sites/<int:site_id>', methods=['DELETE'])
@require_auth
def api_sites_delete(site_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT domain, site_user, site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'code': 404, 'msg': '站点不存在'})

    domain, site_user, site_path = row

    # 1. 删除 Nginx 配置
    remove_nginx_config(domain)

    # 2. 删除系统用户和目录
    delete_site_user(site_user)
    import shutil
    parent_path = os.path.dirname(site_path)
    if os.path.exists(os.path.join(parent_path, site_user)):
        shutil.rmtree(os.path.join(parent_path, site_user), ignore_errors=True)

    # 3. 删除数据库
    cur2 = conn.execute("SELECT name, db_user FROM databases WHERE site_id = ?", (site_id,))
    for db_row in cur2.fetchall():
        delete_mysql_db(db_row[0], db_row[1])

    # 4. 删除站点记录
    conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()

    write_log('site_delete', f'删除站点 {domain}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '站点已删除'})

@app.route('/api/sites/<int:site_id>/start', methods=['POST'])
@require_auth
def api_sites_start(site_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT domain FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})
    _, msg = nginx_reload()
    return jsonify({'code': 0, 'msg': msg or '已启动'})

@app.route('/api/sites/<int:site_id>/stop', methods=['POST'])
@require_auth
def api_sites_stop(site_id):
    return jsonify({'code': 0, 'msg': '停止站点需要 reload nginx，建议通过 Nginx 命令管理'})

@app.route('/api/sites/<int:site_id>/ssl', methods=['POST'])
@require_auth
def api_sites_ssl(site_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT domain, site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})
    domain, site_path = row
    return jsonify({'code': 0, 'msg': 'SSL 功能开发中，请手动配置 certbot'})

# ==========================
# 数据库
# ==========================

@app.route('/api/databases', methods=['GET'])
@require_auth
def api_databases_list():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM databases ORDER BY id DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return jsonify({'code': 0, 'data': rows})

@app.route('/api/databases', methods=['POST'])
@require_auth
def api_databases_create():
    data = request.json or {}
    site_id = data.get('site_id')
    db_name = data.get('name', '').strip()
    db_user = data.get('user', '').strip()
    db_pass = data.get('pass', '')

    if not all([site_id, db_name, db_user, db_pass]):
        return jsonify({'code': 400, 'msg': '参数不完整'})

    ok, msg = create_mysql_db(db_name, db_user, db_pass)
    if not ok:
        return jsonify({'code': 500, 'msg': msg})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""INSERT INTO databases (site_id, name, db_user, db_pass) VALUES (?, ?, ?, ?)""",
                       (site_id, db_name, db_user, db_pass))
    conn.commit()
    db_id = cur.lastrowid
    conn.close()

    write_log('db_create', f'创建数据库 {db_name}', request.remote_addr)
    _sync_pma_bridge()
    return jsonify({'code': 0, 'msg': '数据库创建成功', 'data': {'id': db_id}})

@app.route('/api/databases/<int:db_id>', methods=['DELETE'])
@require_auth
def api_databases_delete(db_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT name, db_user FROM databases WHERE id = ?", (db_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'code': 404, 'msg': '数据库不存在'})
    delete_mysql_db(row[0], row[1])
    conn.execute("DELETE FROM databases WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()
    write_log('db_delete', f'删除数据库 {row[0]}', request.remote_addr)
    _sync_pma_bridge()
    return jsonify({'code': 0, 'msg': '数据库已删除'})

# ==========================
# 备份
# ==========================

@app.route('/api/backups', methods=['GET'])
@require_auth
def api_backups_list():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""SELECT b.*, s.domain FROM backups b
                          LEFT JOIN sites s ON b.site_id = s.id
                          ORDER BY b.id DESC LIMIT 50""")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return jsonify({'code': 0, 'data': rows})

@app.route('/api/backups', methods=['POST'])
@require_auth
def api_backups_create():
    data = request.json or {}
    site_id = data.get('site_id')

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path, domain FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    site_path, domain = row

    # 查找该站点的数据库
    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.execute("SELECT name, db_user, db_pass FROM databases WHERE site_id = ?", (site_id,))
    db_row = cur2.fetchone()
    conn2.close()

    db_name, db_user, db_pass = (db_row if db_row else (None, None, None))

    ok, path, size = backup_site(site_path, domain, db_name, db_user, db_pass)
    if not ok:
        return jsonify({'code': 500, 'msg': f'备份失败: {path}'})

    conn3 = sqlite3.connect(DB_PATH)
    cur3 = conn3.execute("INSERT INTO backups (site_id, type, file_path, size, status) VALUES (?, ?, ?, ?, ?)",
                  (site_id, 'local', path, size, 'success'))
    conn3.commit()
    backup_id = cur3.lastrowid
    conn3.close()

    write_log('backup', f'备份站点 {domain}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '备份成功', 'data': {'id': backup_id, 'path': path, 'size': size}})

@app.route('/api/backups/<int:backup_id>/restore', methods=['POST'])
@require_auth
def api_backups_restore(backup_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT file_path, site_id FROM backups WHERE id = ?", (backup_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '备份不存在'})

    file_path, site_id = row

    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    site_row = cur2.fetchone()
    conn2.close()

    if not site_row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    ok, msg = restore_backup(file_path, site_row[0], str(site_id))
    if not ok:
        return jsonify({'code': 500, 'msg': msg})

    write_log('restore', f'恢复备份 {file_path}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '恢复成功'})

# ==========================
# 安全
# ==========================

@app.route('/api/security/logs', methods=['GET'])
@require_auth
def api_security_logs():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM security_logs ORDER BY id DESC LIMIT 100")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return jsonify({'code': 0, 'data': rows})

@app.route('/api/security/status', methods=['GET'])
@require_auth
def api_security_status():
    status = get_security_status()
    return jsonify({'code': 0, 'data': status})

# ==========================
# 文件管理
# ==========================

@app.route('/api/files/list', methods=['GET'])
@require_auth
def api_files_list():
    site_id = request.args.get('site_id', type=int)
    path = request.args.get('path', '')

    if not site_id:
        return jsonify({'code': 400, 'msg': '缺少 site_id'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    base_path = row[0]
    if path:
        target_path = os.path.join(base_path, path)
    else:
        target_path = base_path

    items, err = list_directory(target_path)
    if err:
        return jsonify({'code': 400, 'msg': err})

    return jsonify({'code': 0, 'data': items, 'base_path': base_path})

@app.route('/api/files/read', methods=['GET'])
@require_auth
def api_files_read():
    site_id = request.args.get('site_id', type=int)
    filepath = request.args.get('path', '')

    if not site_id or not filepath:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    full_path = os.path.join(row[0], filepath)
    content, err = read_file(full_path)
    if err:
        return jsonify({'code': 400, 'msg': err})

    return jsonify({'code': 0, 'data': content})

@app.route('/api/files/write', methods=['POST'])
@require_auth
def api_files_write():
    data = request.json or {}
    site_id = data.get('site_id')
    filepath = data.get('path', '')
    content = data.get('content', '')

    if not site_id or not filepath:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    full_path = os.path.join(row[0], filepath)
    ok, msg = write_file(full_path, content)
    if ok:
        write_log('file_edit', f'编辑文件 {filepath}', request.remote_addr)

    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/files/upload', methods=['POST'])
@require_auth
def api_files_upload():
    site_id = request.form.get('site_id', type=int)
    path = request.form.get('path', '')
    file = request.files.get('file')

    if not site_id or not file:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    upload_dir = os.path.join(row[0], path) if path else row[0]
    ok, msg = upload_file(upload_dir, file, file.filename)
    if ok:
        write_log('file_upload', f'上传文件 {file.filename}', request.remote_addr)

    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/files/delete', methods=['POST'])
@require_auth
def api_files_delete():
    data = request.json or {}
    site_id = data.get('site_id')
    filepath = data.get('path', '')

    if not site_id or not filepath:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    full_path = os.path.join(row[0], filepath)
    ok, msg = delete_file(full_path)
    if ok:
        write_log('file_delete', f'删除文件 {filepath}', request.remote_addr)

    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/files/mkdir', methods=['POST'])
@require_auth
def api_files_mkdir():
    data = request.json or {}
    site_id = data.get('site_id')
    dirpath = data.get('path', '')
    dirname = data.get('name', '')

    if not site_id or not dirname:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    full_path = os.path.join(row[0], dirpath) if dirpath else row[0]
    ok, msg = create_directory(full_path, dirname)

    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/files/chmod', methods=['POST'])
@require_auth
def api_files_chmod():
    data = request.json or {}
    site_id = data.get('site_id')
    filepath = data.get('path', '')
    mode = data.get('mode', 0)

    if not site_id or not filepath:
        return jsonify({'code': 400, 'msg': '参数不完整'})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '站点不存在'})

    full_path = os.path.join(row[0], filepath)
    ok, msg = chmod_file(full_path, mode)

    return jsonify({'code': 0 if ok else 400, 'msg': msg})

# ==========================
# SSL 证书
# ==========================

@app.route('/api/ssl/certs', methods=['GET'])
@require_auth
def api_ssl_list():
    certs = get_all_certs()
    status = check_certs_status()
    return jsonify({'code': 0, 'data': certs, 'status': status})

@app.route('/api/ssl/apply', methods=['POST'])
@require_auth
def api_ssl_apply():
    """
    v1.3.25 改：申请 SSL 走任务流（不再同步等 certbot，可能耗时 1-3 分钟）
    任务类型: ssl_apply，target: <domain>
    完成后用 on_complete 钩子自动部署 SSL
    """
    data = request.json or {}
    site_id = data.get('site_id')
    domain = data.get('domain', '').strip()

    # v1.3.10 修复：原 bug 是 if not site_id: skip → domain 永远空字符串
    if not domain and site_id:
        # 通过 site_id 反查域名
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT domain FROM sites WHERE id = ?", (site_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({'code': 404, 'msg': '站点不存在'})
        domain = row[0]
    elif not domain and not site_id:
        return jsonify({'code': 400, 'msg': 'site_id 和 domain 至少传一个'})

    if not domain:
        return jsonify({'code': 400, 'msg': '域名不能为空'})

    # 查重：同一个域名不能同时跑两个任务
    existing = get_running_task_by_type('ssl_apply', domain)
    if existing:
        return jsonify({'code': 400, 'msg': f'该域名证书申请正在进行中（任务 ID {existing}）', 'data': {'task_id': existing}})

    # 构造 certbot 命令 + 后续 deploy 命令（连入一个 shell 脚本，由任务流串行执行）
    # 这样任务失败也能看到 deploy 不会跑的日志
    real_site_path = None
    try:
        real_site_path = _get_real_site_path(domain, site_id)
    except Exception as e:
        print(f'[ssl_apply] _get_real_site_path error: {e}', flush=True)

    if not real_site_path:
        return jsonify({'code': 400, 'msg': f'找不到站点 {domain} 的真实路径，请确认站点已创建'})

    # 任务命令: certbot 申请 → 如果成功调 deploy_ssl
    cmd = [
        'bash', '-c',
        f'''
set -e
echo "[1/3] 准备 .well-known 验证目录..."
mkdir -p {real_site_path}/.well-known/acme-challenge

echo "[2/3] 调用 certbot 申请证书（以 webroot 模式，需要 30-60s）..."
sudo certbot certonly --webroot -w {real_site_path} -d {domain} --agree-tos --non-interactive --email admin@{domain}

echo "[3/3] 证书生成成功，部署到 nginx..."
'''
    ]

    def _on_ssl_done(task_id, status):
        # v1.3.34 修复：on_complete 钩子在后台线程跑，没有 Flask request context
        # 不能用 request.remote_addr,传 None 给 write_log
        if status == 'success':
            try:
                ok, msg = deploy_ssl(domain)
                if ok:
                    write_log('ssl_apply', f'申请+部署 SSL 证书 {domain} 成功', None)
                else:
                    write_log('ssl_apply', f'证书已申请但部署 nginx 失败 {domain}: {msg}', None)
            except Exception as e:
                write_log('ssl_apply', f'证书部署异常 {domain}: {e}', None)

    task_id = create_task('ssl_apply', domain, cmd, on_complete=_on_ssl_done)
    write_log('ssl_apply', f'启动 SSL 申请任务 {domain}（task_id={task_id}）', request.remote_addr)
    return jsonify({'code': 0, 'msg': f'证书申请任务已启动（task_id={task_id}），请查看进度', 'data': {'task_id': task_id}})

@app.route('/api/ssl/renew/<int:cert_id>', methods=['POST'])
@require_auth
def api_ssl_renew(cert_id):
    ok, msg = renew_cert(cert_id=cert_id)
    if ok:
        write_log('ssl_renew', f'续期证书 ID {cert_id}', request.remote_addr)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/ssl/renew-all', methods=['POST'])
@require_auth
def api_ssl_renew_all():
    success, fail_count, fail_list = renew_all_expiring(days_before=30)
    msg = f'续期完成：成功 {success} 个'
    if fail_count > 0:
        msg += f'，失败 {fail_count} 个：{"; ".join(fail_list)}'
    write_log('ssl_renew_all', msg, request.remote_addr)
    return jsonify({'code': 0, 'msg': msg, 'success': success, 'failed': fail_count})

@app.route('/api/ssl/deploy/<domain>', methods=['POST'])
@require_auth
def api_ssl_deploy(domain):
    ok, msg = deploy_ssl(domain)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

# ==========================
# 定时任务（安全自动更新 + SSL 续期）
# ==========================

@app.route('/api/cron/run', methods=['POST'])
@require_auth
def api_cron_run():
    """v1.3.20+：手动触发定时任务走任务流（不阻塞 HTTP）"""
    # 检查是否已在跑
    existing = get_running_task_by_type('security_update')
    if existing:
        return jsonify({'code': 400, 'msg': f'更新任务正在进行（ID {existing}）', 'data': {'task_id': existing}})

    # 安全更新走任务流（跟 /api/security/update 一样）
    cmd = ['sudo', 'apt-get', 'update', '-y', '-q']
    task_id = create_task('security_update', 'system', cmd)
    write_log('cron_run', f'启动手动安全更新任务（task_id={task_id}）', request.remote_addr)
    return jsonify({'code': 0, 'msg': f'安全更新任务已启动（task_id={task_id}），请去任务流查看进度', 'data': {'task_id': task_id}})



# ==========================
# 定时任务 API
# ==========================

@app.route('/api/cron/jobs', methods=['GET'])
@require_auth
def api_cron_list():
    jobs = get_all_crons()
    return jsonify({'code': 0, 'data': jobs})

@app.route('/api/cron/jobs', methods=['POST'])
@require_auth
def api_cron_create():
    data = request.json or {}
    site_id = data.get('site_id')
    name = data.get('name', '').strip()
    schedule = data.get('schedule', '').strip()
    command = data.get('command', '').strip()

    if not all([site_id, name, schedule, command]):
        return jsonify({'code': 400, 'msg': '参数不完整'})

    # 验证 cron 表达式
    ok, err = validate_cron_expression(schedule)
    if not ok:
        return jsonify({'code': 400, 'msg': f'Cron 格式错误: {err}'})

    cron_id, msg = create_cron(site_id, name, schedule, command)
    if cron_id:
        write_log('cron_create', f'创建定时任务 {name} ({schedule})', request.remote_addr)
        return jsonify({'code': 0, 'msg': msg, 'data': {'id': cron_id}})
    else:
        return jsonify({'code': 400, 'msg': msg})

@app.route('/api/cron/jobs/<int:cron_id>', methods=['DELETE'])
@require_auth
def api_cron_delete(cron_id):
    ok, msg = delete_cron(cron_id)
    if ok:
        write_log('cron_delete', f'删除定时任务 ID {cron_id}', request.remote_addr)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/cron/jobs/<int:cron_id>/toggle', methods=['POST'])
@require_auth
def api_cron_toggle(cron_id):
    data = request.json or {}
    enabled = data.get('enabled', True)
    ok, msg = enable_cron(cron_id, enabled)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/cron/jobs/<int:cron_id>/run', methods=['POST'])
@require_auth
def api_cron_run_now(cron_id):
    ok, msg = run_cron_now(cron_id)
    if ok:
        write_log('cron_run_now', f'手动执行定时任务 ID {cron_id}', request.remote_addr)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

# ==========================
# 远程备份 API
# ==========================

@app.route('/api/backups/remote', methods=['POST'])
@require_auth
def api_remote_backup():
    data = request.json or {}
    site_id = data.get('site_id')
    remote_host = data.get('remote_host', '').strip()
    remote_user = data.get('remote_user', '').strip()
    remote_port = data.get('remote_port', 22)
    remote_path = data.get('remote_path', '').strip()
    key_path = data.get('key_path', '').strip()

    if not all([site_id, remote_host, remote_user, remote_path]):
        return jsonify({'code': 400, 'msg': '参数不完整'})

    ok, msg = run_remote_backup(
        site_id, remote_host, remote_user, remote_port,
        remote_path, key_path=key_path if key_path else None
    )
    if ok:
        return jsonify({'code': 0, 'msg': msg})
    else:
        return jsonify({'code': 400, 'msg': msg})

@app.route('/api/backups/remote/restore/<int:backup_id>', methods=['POST'])
@require_auth
def api_remote_restore(backup_id):
    data = request.json or {}
    remote_host = data.get('remote_host', '').strip()
    remote_user = data.get('remote_user', '').strip()
    remote_port = data.get('remote_port', 22)
    remote_path = data.get('remote_path', '').strip()
    key_path = data.get('key_path', '').strip()

    if not all([remote_host, remote_user, remote_path]):
        return jsonify({'code': 400, 'msg': '参数不完整'})

    ok, msg = sync_restore(
        backup_id, remote_host, remote_user, remote_port,
        remote_path, key_path=key_path if key_path else None
    )
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/backups/remote/test', methods=['POST'])
@require_auth
def api_test_rsync():
    data = request.json or {}
    host = data.get('host', '').strip()
    port = data.get('port', 22)
    user = data.get('user', '').strip()
    key_path = data.get('key_path', '').strip()

    if not host or not user:
        return jsonify({'code': 400, 'msg': '主机和用户名不能为空'})

    ok, msg = test_rsync_connection(host, port, user, key_path if key_path else None)
    return jsonify({'code': 0 if ok else 400, 'msg': msg})

@app.route('/api/backups/stats', methods=['GET'])
@require_auth
def api_backup_stats():
    stats = get_backup_stats()
    return jsonify({'code': 0, 'data': stats})

# ==========================
# 设置
# ==========================


@app.route('/api/settings', methods=['GET'])
@require_auth
def api_settings_get():
    cfg = load_config()
    return jsonify({'code': 0, 'data': cfg})

@app.route('/api/settings', methods=['PUT'])
@require_auth
def api_settings_put():
    """v1.3.19+：修双重存储 bug——既写 tpanel.conf 又同步 sqlite settings"""
    data = request.json or {}
    allowed_keys = [
        'panel_domain', 'default_php', 'backup_retention_days',
        'auto_ssl_renew', 'security_auto_update', 'firewall_enabled'
    ]
    cfg = load_config()
    cfg.update({k: v for k, v in data.items() if k in allowed_keys})
    save_config(cfg)

    # 关键修复：把要进 check_panel_domain / get_panel_domain 的字段也写 sqlite settings
    # 否则 get_panel_domain() 永远从 sqlite 读到旧值
    if 'panel_domain' in data:
        set_setting('panel_domain', data['panel_domain'] or '')
    # 其他字段也同步（保证 sqlite 跟 conf 一致）
    for k in allowed_keys:
        if k in data:
            v = data[k]
            if isinstance(v, bool):
                v = '1' if v else '0'
            elif v is None:
                v = ''
            set_setting(k, str(v))

    return jsonify({'code': 0, 'msg': '设置已保存'})

# ==========================
# 软件市场 + 任务管理（v1.3.10 新增）
# ==========================
from task_manager import (
    list_software, get_software, get_apt_packages,
    create_task, get_task, get_running_task_by_type,
    init_software_table, get_apt_cmd,
    setup_phpmyadmin_nginx
)
import json
import time

@app.route('/api/software/list', methods=['GET'])
@require_auth
def api_software_list():
    return jsonify({'code': 0, 'data': list_software()})

@app.route('/api/software/install/<name>', methods=['POST'])
@require_auth
def api_software_install(name):
    sw = get_software(name)
    if not sw:
        return jsonify({'code': 404, 'msg': '软件不在白名单'})
    if sw['installed']:
        return jsonify({'code': 400, 'msg': f'{sw["display_name"]} 已经安装了'})
    # 防止并发装同一个
    existing = get_running_task_by_type('software_install', name)
    if existing:
        return jsonify({'code': 400, 'msg': f'该软件正在安装中（任务 ID {existing}）', 'data': {'task_id': existing}})
    pkgs = get_apt_packages(name)
    if not pkgs:
        return jsonify({'code': 500, 'msg': '未找到该软件包名'})
    pkg_list = pkgs.split(',')
    try:
        cmd = get_apt_cmd() + ['install'] + pkg_list
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})
    # v1.3.29: 装 PHP 走 on_complete 钩子自动配 FPM listen 端口
    on_complete = None
    if name.startswith('php') and name[3:].replace('.', '').isdigit():
        # name = 'php7.4' / 'php8.3' → php_version = '7.4' / '8.3'
        php_version = name[3:]

        def _on_php_installed(task_id, status, pv=php_version):
            """on_complete 钩子：装完后改 FPM listen 端口 + enable + restart"""
            if status != 'success':
                return
            ok, msg = setup_php_fpm_listen(pv)
            if ok:
                write_log('php_install', f'PHP {pv} FPM 已配置 {msg}', request.remote_addr)
            else:
                write_log('php_install', f'PHP {pv} FPM 配置失败: {msg}', request.remote_addr)
        on_complete = _on_php_installed
    task_id = create_task('software_install', name, cmd, on_complete=on_complete)
    write_log('software_install', f'开始安装 {name}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '安装任务已启动', 'data': {'task_id': task_id}})

# v1.3.29: 批量重写所有站点 nginx conf（按照 db 的 php_version 字段用对应端口）
# 用途：手动统一切换所有站点到某个 PHP 版本
@app.route('/api/system/rewrite-all-nginx', methods=['POST'])
@require_auth
def api_rewrite_all_nginx():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, domain, site_path, php_version, ssl_enabled FROM sites").fetchall()
    conn.close()
    rewritten = 0
    failed = []
    for sid, domain, site_path, php_v, ssl in rows:
        # 跳过静态站点
        try:
            conn2 = sqlite3.connect(DB_PATH)
            st = conn2.execute("SELECT site_type FROM sites WHERE id=?", (sid,)).fetchone()
            conn2.close()
            site_type = st[0] if st and st[0] else 'php'
        except Exception:
            site_type = 'php'
        ok, msg = write_nginx_config(domain, site_path, php_v, ssl=bool(ssl), site_type=site_type)
        if ok:
            rewritten += 1
        else:
            failed.append((domain, msg))
    # reload nginx
    code, out, err = _run(['sudo', 'nginx', '-t'])
    if code == 0:
        _run(['sudo', 'nginx', '-s', 'reload'])
    return jsonify({'code': 0, 'msg': f'重写 {rewritten} 个站点 conf，失败 {len(failed)} 个', 'data': {'rewritten': rewritten, 'failed': failed}})

@app.route('/api/software/uninstall/<name>', methods=['POST'])
@require_auth
def api_software_uninstall(name):
    sw = get_software(name)
    if not sw:
        return jsonify({'code': 404, 'msg': '软件不在白名单'})
    pkgs = get_apt_packages(name)
    if not pkgs:
        return jsonify({'code': 500, 'msg': '未找到该软件包名'})
    pkg_list = pkgs.split(',')
    try:
        cmd = get_apt_cmd() + ['remove'] + pkg_list
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})
    existing = get_running_task_by_type('software_uninstall', name)
    if existing:
        return jsonify({'code': 400, 'msg': f'正在卸载中（任务 ID {existing}）'})
    task_id = create_task('software_uninstall', name, cmd)
    write_log('software_uninstall', f'开始卸载 {name}', request.remote_addr)
    return jsonify({'code': 0, 'msg': '卸载任务已启动', 'data': {'task_id': task_id}})

@app.route('/api/tasks/<int:task_id>', methods=['GET'])
@require_auth
def api_task_get(task_id):
    t = get_task(task_id)
    if not t:
        return jsonify({'code': 404, 'msg': '任务不存在'})
    return jsonify({'code': 0, 'data': t})

@app.route('/api/tasks/<int:task_id>/stream', methods=['GET'])
@require_auth
def api_task_stream(task_id):
    """SSE 流，推送任务日志和状态"""
    def generate():
        last_log_len = 0
        # 先发当前状态
        t = get_task(task_id)
        if not t:
            yield f"data: {json.dumps({'type': 'error', 'msg': '任务不存在'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'status', 'status': t['status'], 'log': t['log']})}\n\n"
        last_log_len = len(t['log'])
        # 轮询直到完成
        for _ in range(1800):  # 最多 30 分钟
            time.sleep(1)
            t = get_task(task_id)
            if not t:
                yield f"data: {json.dumps({'type': 'error', 'msg': '任务丢失'})}\n\n"
                return
            if len(t['log']) != last_log_len:
                yield f"data: {json.dumps({'type': 'log', 'log': t['log'][last_log_len:]})}\n\n"
                last_log_len = len(t['log'])
            if t['status'] in ('success', 'failed'):
                yield f"data: {json.dumps({'type': 'done', 'status': t['status'], 'exit_code': t['exit_code']})}\n\n"
                return
        yield f"data: {json.dumps({'type': 'error', 'msg': 'SSE 超时'})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

# ==========================
# phpMyAdmin（v1.3.10 新增 - 装完自动配 Nginx 8443）
# ==========================
@app.route('/api/phpmyadmin/status', methods=['GET'])
@require_auth
def api_phpmyadmin_status():
    sw = get_software('phpmyadmin')
    if not sw:
        return jsonify({'code': 0, 'data': {'installed': False, 'url': ''}})
    # v1.3.23 修复：phpMyAdmin URL 构造不再用 hostname 查到的 127.0.0.1
    # 优先顺序：面板域名 > 用户当前访问的 host > 本机获取的 IP
    import socket
    panel_dom = get_panel_domain().strip()
    if panel_dom:
        host = panel_dom
    else:
        # 从 request.host 拆出 hostname（去掉端口）
        try:
            host = request.host.split(':')[0] if request.host else ''
        except Exception:
            host = ''
        if not host or host in ('127.0.0.1', 'localhost'):
            # fallback: 拿第一个非 loopback 的网卡 IP
            try:
                ip_fallback = socket.gethostbyname(socket.gethostname())
                if ip_fallback and not ip_fallback.startswith('127.'):
                    host = ip_fallback
                else:
                    host = '127.0.0.1'
            except Exception:
                host = '127.0.0.1'
    # 三个条件都满足才算真装好：
    # 1. software 表标记 installed
    # 2. phpMyAdmin 文件实际存在（防 sw 状态滞后）
    # 3. Nginx 8443 反代配置已写入
    files_exist = (
        os.path.isdir('/usr/share/phpmyadmin')
        and os.path.exists('/usr/share/phpmyadmin/index.php')
    )
    nginx_ok = os.path.exists('/etc/nginx/sites-enabled/phpmyadmin.conf')
    really_installed = bool(sw['installed']) and files_exist and nginx_ok
    url = f'http://{host}:8443/' if really_installed else ''
    return jsonify({'code': 0, 'data': {
        'installed': really_installed,
        'url': url,
        'files_exist': files_exist,
        'nginx_ok': nginx_ok,
        'sw_installed': bool(sw['installed'])
    }})

@app.route('/api/phpmyadmin/install', methods=['POST'])
@require_auth
def api_phpmyadmin_install():
    existing = get_running_task_by_type('software_install', 'phpmyadmin')
    if existing:
        return jsonify({'code': 400, 'msg': f'phpMyAdmin 正在安装（任务 ID {existing}）', 'data': {'task_id': existing}})
    pkgs = get_apt_packages('phpmyadmin')
    if not pkgs:
        return jsonify({'code': 500, 'msg': '未找到包名'})
    # 装 phpmyadmin（-q 避免交互）
    try:
        cmd = ['sudo', 'apt-get', '-y', '-q', '-o', 'Dpkg::Options::=--force-confdef', '-o', 'Dpkg::Options::=--force-confnew', 'install'] + pkgs.split(',')
    except Exception:
        cmd = ['sudo', 'apt-get', '-y', 'install'] + pkgs.split(',')
    # v1.3.10：装完后用 on_complete 钩子自动写 Nginx 8443 反代
    def _on_pma_installed(task_id, status):
        if status == 'success':
            setup_phpmyadmin_nginx(task_id=task_id)
    task_id = create_task('software_install', 'phpmyadmin', cmd, on_complete=_on_pma_installed)
    return jsonify({'code': 0, 'msg': 'phpMyAdmin 安装任务已启动（装完会自动配置 Nginx 8443）', 'data': {'task_id': task_id}})

# ==========================
# v1.3.34 数据库改密 + phpMyAdmin 真自动登录
# ==========================

# 数据库改密（更新 MySQL + sqlite 都改）
@app.route('/api/databases/<int:db_id>/password', methods=['POST'])
@require_auth
def api_change_db_password(db_id):
    data = request.json or {}
    new_pass = data.get('new_pass', '')
    if not new_pass or len(new_pass) < 6:
        return jsonify({'code': 400, 'msg': '新密码至少 6 位'})
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT name, db_user FROM databases WHERE id = ?", (db_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'code': 404, 'msg': '数据库不存在'})
    db_name, db_user = row[0], row[1]
    ok, msg = change_db_password(db_user, new_pass)
    if not ok:
        conn.close()
        return jsonify({'code': 500, 'msg': msg})
    # 同步更新 sqlite（备份、phpMyAdmin 自动填、面板显示都用这个）
    conn.execute("UPDATE databases SET db_pass = ? WHERE id = ?", (new_pass, db_id))
    conn.commit()
    conn.close()
    write_log('db_change_pass', f'修改数据库 {db_name} 密码', request.remote_addr)
    _sync_pma_bridge()
    return jsonify({'code': 0, 'msg': '密码修改成功'})

# v1.3.34: 数据库改密/增删后同步 phpMyAdmin bridge.json
def _sync_pma_bridge():
    import subprocess
    try:
        r = subprocess.run(
            ['sudo', '-u', 'tpanel', 'python3', '/opt/tpanel/backend/sync_pma_bridge.py'],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            write_log('pma_bridge_sync_fail', r.stderr, request.remote_addr)
    except Exception as e:
        write_log('pma_bridge_sync_err', str(e), request.remote_addr)


# 生成临时 token（5 分钟有效，用于 phpMyAdmin 自动登录跳转）
# payload: db_id + exp, HMAC-SHA256 签名
import hmac, hashlib, base64, json, time

def _sign_token(db_id, ttl=300):
    payload = {'db_id': db_id, 'exp': int(time.time()) + ttl}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode()
    return f'{payload_b64}.{sig_b64}'

def _verify_token(token):
    try:
        payload_b64, sig_b64 = token.split('.')
        # v1.3.34: sign 时已带 padding，不再补
        sig = base64.urlsafe_b64decode(sig_b64)
        expected = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get('exp', 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None

@app.route('/api/phpmyadmin/token/<int:db_id>', methods=['GET'])
@require_auth
def api_phpmyadmin_token(db_id):
    """签发一次性 token（5 分钟有效），前端拼 URL 跳 phpMyAdmin"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT name FROM databases WHERE id = ?", (db_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({'code': 404, 'msg': '数据库不存在'})
    token = _sign_token(db_id)
    return jsonify({'code': 0, 'data': {'token': token, 'expires_in': 300}})

# phpMyAdmin Signon 端点（无 auth — SignonURL 走 GET，phpMyAdmin 自动跳过来）
@app.route('/api/phpmyadmin/signon', methods=['GET'])
def api_phpmyadmin_signon():
    """v1.3.34 phpMyAdmin Signon 端点：验 token → 302 到 PHP bridge.php
    bridge.php 启动 PHP session + 写 PMA_single_signon_* + 302 回 phpMyAdmin
    """
    token = request.args.get('token', '')
    db_id = request.args.get('db', '')
    if not token or not db_id:
        return redirect('http://127.0.0.1:8888/login?msg=missing_token')
    # 不在 Python 端验 token（bridge.php 会用 HMAC 验证），直接 302 过去
    return redirect('https://zhangpu.tech/pma/tpanel-bridge.php?token=' + token + '&db=' + db_id)

# phpMyAdmin 退出（清除 cookie + 重定向回面板）
@app.route('/api/phpmyadmin/logout', methods=['GET'])
def api_phpmyadmin_logout():
    resp = redirect('http://127.0.0.1:8888/dashboard')
    resp.delete_cookie('TPanelSignon', domain='127.0.0.1', path='/')
    return resp

# ==========================
# 安全更新（v1.3.10 改为走任务流）
# ==========================
@app.route('/api/security/update', methods=['POST'])
@require_auth
def api_security_update():
    existing = get_running_task_by_type('security_update')
    if existing:
        return jsonify({'code': 400, 'msg': f'更新任务正在进行（ID {existing}）', 'data': {'task_id': existing}})
    try:
        # apt-get update + upgrade -y
        cmd = ['sudo', 'apt-get', 'update', '-y', '-q']
    except Exception:
        cmd = ['sudo', 'apt-get', 'update', '-y']
    task_id = create_task('security_update', 'system', cmd)
    write_log('security_update', '启动系统安全更新', request.remote_addr)
    return jsonify({'code': 0, 'msg': '更新任务已启动', 'data': {'task_id': task_id}})

# ==========================
# v1.3.28: 添加 Sury PHP 第三方源
# 让 Debian 12 也能装 PHP 5.6 / 7.0 / 7.4 / 8.0 / 8.1 / 8.3 / 8.4
# 走任务流，因为 apt update 可能耗时 1-2 分钟
# ==========================
@app.route('/api/system/add-sury-php', methods=['POST'])
@require_auth
def api_add_sury_php():
    # 只支持 Debian/Ubuntu 系
    if not os.path.exists('/etc/debian_version') and not os.path.exists('/etc/lsb-release'):
        return jsonify({'code': 400, 'msg': 'Sury PHP 源仅支持 Debian/Ubuntu 系统'})

    # 检查是否已添加
    if os.path.exists('/etc/apt/sources.list.d/php.list'):
        return jsonify({'code': 400, 'msg': 'Sury PHP 源已添加，无需重复操作'})

    # 查重：同类型任务
    existing = get_running_task_by_type('sury_php', 'sury_php')
    if existing:
        return jsonify({'code': 400, 'msg': f'Sury 源添加任务正在进行（ID {existing}）', 'data': {'task_id': existing}})

    # 任务命令：装 lsb-release + ca-certificates + curl → 下载 GPG keyring → 配置源 → apt update
    cmd = [
        'bash', '-c',
        '''
set -e
echo "[1/5] 装 lsb-release / ca-certificates / curl..."
sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -q lsb-release ca-certificates curl 2>&1
echo "[2/5] 下载 Sury GPG keyring..."
sudo -n curl -sSLo /tmp/debsuryorg-archive-keyring.deb https://packages.sury.org/debsuryorg-archive-keyring.deb
echo "[3/5] 装 keyring..."
sudo -n dpkg -i /tmp/debsuryorg-archive-keyring.deb
echo "[4/5] 写 /etc/apt/sources.list.d/php.list..."
DISTRO=$(lsb_release -sc)
sudo -n sh -c "echo 'deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ ${DISTRO} main' > /etc/apt/sources.list.d/php.list"
echo "[5/5] apt update（可能要 30-90s）..."
sudo -n DEBIAN_FRONTEND=noninteractive apt-get update -q 2>&1
echo "===Sury PHP 源添加完成==="
'''
    ]
    task_id = create_task('sury_php', 'sury_php', cmd)
    write_log('sury_php', '启动添加 Sury PHP 源任务', request.remote_addr)
    return jsonify({'code': 0, 'msg': f'Sury PHP 源添加任务已启动（task_id={task_id}），请查看进度', 'data': {'task_id': task_id}})

# ==========================
# 静态文件
# ==========================

@app.route('/')
def serve_index():
    return app.send_static_file('index.html')

@app.route('/<path:path>')
def serve_static(path):
    full = os.path.join(app.static_folder, path)
    if os.path.exists(full) and not os.path.isdir(full):
        return app.send_static_file(path)
    return app.send_static_file('index.html')

# ==========================
# 健康检查
# ==========================

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
    # 初始化数据库
    from db_init import init_db
    init_db()
    # 生成初始 API token
    if not get_setting('api_token'):
        set_setting('api_token', secrets.token_hex(32))
    print(f"[TPanel] 启动于端口 {port}")
    app.run(host='127.0.0.1', port=port, debug=False)