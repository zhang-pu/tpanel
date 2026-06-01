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

from flask import Flask, jsonify, request, session, redirect
from flask_cors import CORS

# 导入各模块
from config import DB_PATH, load_config, save_config, set_setting, get_setting
from system import (
    nginx_status, nginx_reload, mysql_status,
    create_site_user, delete_site_user,
    write_nginx_config, remove_nginx_config,
    create_mysql_db, delete_mysql_db,
    backup_site, restore_backup,
    run_security_update, get_security_status, get_system_stats, write_log
)
from file_manager import list_directory, read_file, write_file, upload_file, delete_file, chmod_file, create_directory
from ssl_manager import get_all_certs, apply_letsencrypt, renew_cert, renew_all_expiring, deploy_ssl, check_certs_status
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
    if not allowed:
        return  # 未绑定，不限制

    host = request.host.lower()
    allowed_clean = allowed.lower().strip().split(':')[0]
    host_clean = host.split(':')[0]

    safe = ['localhost', '127.0.0.1', '::1']
    if host_clean in safe:
        return

    if host_clean != allowed_clean:
        return jsonify({'code': 403, 'msg': f'面板已绑定域名 {allowed}，请使用该域名访问'}), 403

# ==========================
# 装饰器
# ==========================

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
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
    php_version = data.get('php_version', '8.1')
    site_user = domain.replace('.', '_')

    if not domain:
        return jsonify({'code': 400, 'msg': '域名不能为空'})

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

    # 2. 创建目录并写入 index.php
    os.makedirs(site_path, exist_ok=True)
    os.makedirs(f'{site_path}/public', exist_ok=True)
    with open(f'{site_path}/public/index.php', 'w') as f:
        f.write(f'<?php\n// Site: {domain}\n// Managed by TPanel\nphpinfo();\n')
    os.makedirs(f'{site_path}/logs', exist_ok=True)

    # 3. 写 Nginx 配置
    ok, msg = write_nginx_config(domain, f'{site_path}/public', php_version)
    if not ok:
        conn.close()
        return jsonify({'code': 500, 'msg': f'Nginx 配置失败: {msg}'})

    # 4. 写入数据库
    cur.execute("""INSERT INTO sites (name, domain, site_user, site_path, php_version, status)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (name, domain, site_user, f'{site_path}/public', php_version, 'running'))
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
    conn3.execute("INSERT INTO backups (site_id, type, file_path, size, status) VALUES (?, ?, ?, ?, ?)",
                  (site_id, 'local', path, size, 'success'))
    conn3.commit()
    backup_id = conn3.lastrowid
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

@app.route('/api/security/update', methods=['POST'])
@require_auth
def api_security_run_update():
    ok, msg = run_security_update()
    write_log('security_update', msg, request.remote_addr)
    return jsonify({'code': 0 if ok else 500, 'msg': msg})

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
    data = request.json or {}
    site_id = data.get('site_id')
    domain = data.get('domain', '')

    if not site_id:
        # 查找站点
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT domain, site_path FROM sites WHERE id = ?", (site_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({'code': 404, 'msg': '站点不存在'})
        domain = row[0]

    if not domain:
        return jsonify({'code': 400, 'msg': '域名不能为空'})

    ok, msg = apply_letsencrypt(site_id, domain)
    if ok:
        # 部署 SSL
        deploy_ok, deploy_msg = deploy_ssl(domain)
        write_log('ssl_apply', f'申请 SSL 证书 {domain}', request.remote_addr)
        return jsonify({'code': 0, 'msg': f'证书申请成功，{deploy_msg}'})
    else:
        return jsonify({'code': 400, 'msg': msg})

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
    """手动触发定时任务（安全更新 + SSL 续期）"""
    # 安全更新
    ok_sec, msg_sec = run_security_update()

    # SSL 续期
    success_ssl, fail_ssl, fail_list = renew_all_expiring(days_before=30)

    msg = f'安全更新：{"成功" if ok_sec else "失败：" + msg_sec}。'
    msg += f' SSL 续期：成功 {success_ssl} 个'
    if fail_ssl > 0:
        msg += f'，失败 {fail_ssl} 个'

    write_log('cron_run', msg, '')
    return jsonify({'code': 0, 'msg': msg})



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
    data = request.json or {}
    cfg = load_config()
    cfg.update({k: v for k, v in data.items() if k in [
        'panel_domain', 'default_php', 'backup_retention_days',
        'auto_ssl_renew', 'security_auto_update', 'firewall_enabled'
    ]})
    save_config(cfg)
    return jsonify({'code': 0, 'msg': '设置已保存'})

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