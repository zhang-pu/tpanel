"""
TPanel - SSL 证书管理 & 自动续期
"""
import os
import sqlite3
import subprocess
import re
from datetime import datetime, timedelta
from config import DB_PATH, SSL_DIR

LETSENCRYPT_PATH = '/etc/letsencrypt/live'

def _get_real_site_path(domain, site_id):
    """
    v1.3.24 修复：查 sqlite 拿站点的真实 site_path（里面是 zhangpu_tech 之类的下划线版），
    这样 certbot 写 challenge 文件的路径才跟 nginx root 指向一致
    返回 None 表示找不到（会回退到硬编码的 /opt/tpanel/sites/<domain>/public）
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        if site_id:
            cur = conn.execute("SELECT site_path FROM sites WHERE id = ?", (site_id,))
        else:
            cur = conn.execute("SELECT site_path FROM sites WHERE domain = ?", (domain,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            p = row[0]
            # 确保末尾有 /public（site_path 存的可能就是 /public）
            if not p.rstrip('/').endswith('/public'):
                p = p.rstrip('/') + '/public'
            if os.path.isdir(p):
                return p
    except Exception as e:
        print(f'[ssl] _get_real_site_path failed: {e}', flush=True)
    return None

def _run(cmd, timeout=120, shell=False):
    try:
        if isinstance(cmd, str) and not shell:
            cmd = cmd.split()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out'
    except Exception as e:
        return -1, '', str(e)

def get_cert_info(cert_path):
    """从 PEM 文件读取证书信息（到期日期等）"""
    if not os.path.exists(cert_path):
        return None

    code, out, err = _run([
        'openssl', 'x509', '-in', cert_path,
        '-noout', '-dates', '-enddate'
    ], shell=False)

    expire_str = None
    if code == 0:
        for line in out.split('\n'):
            if 'notAfter=' in line:
                expire_str = line.split('=')[1].strip()
                break

    if expire_str:
        try:
            expire_date = datetime.strptime(expire_str, '%b %d %H:%M:%S %Y %Z')
            return {
                'expire_date': expire_date.strftime('%Y-%m-%d'),
                'days_left': (expire_date - datetime.now()).days,
                'expire_raw': expire_str,
            }
        except Exception:
            pass

    return {'expire_date': '未知', 'days_left': 0, 'expire_raw': expire_str}

def get_all_certs():
    """获取所有证书（含到期信息）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM ssl_certs ORDER BY id DESC")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()

    result = []
    for cert in rows:
        info = get_cert_info(cert['cert_path'])
        cert.update(info or {})
        result.append(cert)

    return result

def apply_letsencrypt(site_id, domain):
    """
    为站点申请 Let's Encrypt 证书
    流程：创建验证目录 → 生成 cert → 部署 nginx 配置 → 写入数据库
    """
    # v1.3.24 修复：不要再硬编码 /opt/tpanel/sites/<domain>/public
    # 建站时 domain 里的 . 被换成 _（zhangpu.tech → zhangpu_tech），
    # certbot 写到 /opt/tpanel/sites/zhangpu.tech/（空目录），
    # 但 nginx root 指向 zhangpu_tech/，LE 服务器拉 403
    site_path = _get_real_site_path(domain, site_id)
    le_dir = os.path.join(SSL_DIR, domain)
    os.makedirs(le_dir, exist_ok=True)

    # 写入 HTTP 验证文件到站点目录
    well_known = os.path.join(site_path, '.well-known', 'acme-challenge')
    os.makedirs(well_known, exist_ok=True)

    # 先测试 nginx 配置能访问到验证文件
    nginx_conf = f'''# SSL verification - {domain}
server {{
    listen 80;
    server_name {domain};
    root {site_path};

    location /.well-known/acme-challenge/ {{
        alias {well_known}/;
        try_files $uri =404;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}
'''
    conf_path = f'/etc/nginx/sites-available/{domain}.ssl.conf'
    # v1.3.21+：用 sudo mv 写 /etc/nginx/sites-available
    tmp_conf = f'/tmp/tpanel_ssl_{domain}.conf'
    with open(tmp_conf, 'w') as f:
        f.write(nginx_conf)
    code, out, err = _run(['sudo', 'mv', tmp_conf, conf_path])
    if code != 0:
        return False, f'写 SSL conf 失败: {err}'

    enabled_path = f'/etc/nginx/sites-enabled/{domain}.ssl.conf'
    if os.path.exists(enabled_path):
        _run(['sudo', 'rm', '-f', enabled_path])
    _run(['sudo', 'ln', '-sf', conf_path, enabled_path])

    code, out, err = _run(['sudo', 'nginx', '-t'])
    if code != 0:
        return False, f'Nginx 配置错误: {err}'

    _run(['sudo', 'nginx', '-s', 'reload'])

    # 申请证书（standalone 模式 + webroot）
    # v1.3.25 修复：去掉 --cert-path/--key-path/--chain-path 自定义路径
    # certbot 会忽略这些路径或写到默认位置（/etc/letsencrypt/live/<domain>/），
    # 导致 TPanel 去 /opt/tpanel/ssl/<domain>/ 找时拿不到，报"证书文件未生成"
    cmd = [
        'sudo', 'certbot', 'certonly',
        '--webroot',
        '-w', site_path,
        '-d', domain,
        '--agree-tos',
        '--non-interactive',
        '--email', f'admin@{domain}',
    ]

    code, out, err = _run(cmd, timeout=120)

    if code != 0:
        # 清理失败配置（v1.3.21+：用 sudo 删软链）
        if os.path.exists(enabled_path):
            _run(['sudo', 'rm', '-f', enabled_path])
        return False, f'证书申请失败: {err}'

    # v1.3.25: certbot 默认写到 /etc/letsencrypt/live/<domain>/，从那里读
    le_live = f'/etc/letsencrypt/live/{domain}'
    cert_path = os.path.join(le_live, 'fullchain.pem')
    key_path = os.path.join(le_live, 'privkey.pem')

    if not os.path.exists(cert_path):
        return False, '证书文件未生成'

    # 写入数据库
    info = get_cert_info(cert_path)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""INSERT INTO ssl_certs (site_id, domain, cert_path, key_path, expire_date, auto_renew)
                          VALUES (?, ?, ?, ?, ?, 1)""",
                       (site_id, domain, cert_path, key_path, info['expire_date'] if info else ''))
    conn.commit()
    conn.close()

    return True, f'证书申请成功，到期：{info["expire_date"] if info else "未知"}'

def renew_cert(cert_id=None, domain=None):
    """
    续期证书（certbot renew）
    """
    if cert_id:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT domain FROM ssl_certs WHERE id = ?", (cert_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            domain = row[0]
    elif domain:
        pass
    else:
        return False, '请指定证书 ID 或域名'

    # certbot renew 只续期 30 天内到期的证书
    code, out, err = _run(
        ['certbot', 'renew', '--cert-name', domain, '--quiet'],
        timeout=120
    )

    if code != 0 and 'No renewals attempted' not in out and 'already valid' not in out:
        return False, f'续期失败: {err}'

    # 更新到期日期
    le_dir = os.path.join(SSL_DIR, domain)
    cert_path = os.path.join(le_dir, 'fullchain.pem')
    info = get_cert_info(cert_path)

    if info:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("UPDATE ssl_certs SET expire_date = ? WHERE domain = ?",
                          (info['expire_date'], domain))
        conn.commit()
        conn.close()

    return True, f'证书已续期，新到期：{info["expire_date"] if info else "未知"}'

def renew_all_expiring(days_before=30):
    """
    续期所有即将到期的证书（供定时任务调用）
    返回：(成功数量, 失败数量, 详情列表)
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM ssl_certs WHERE auto_renew = 1")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return 0, 0, []

    success, fail = 0, []
    for row in rows:
        cert_id, site_id, domain = row[0], row[1], row[2]
        info = get_cert_info(row[3])  # cert_path

        # 检查是否在 30 天内到期
        if info and info['days_left'] <= days_before:
            ok, msg = renew_cert(cert_id=cert_id, domain=domain)
            if ok:
                success += 1
            else:
                fail.append(f'{domain}: {msg}')
        elif not info or info['days_left'] > days_before:
            # 证书已过期或不存在
            pass

    return success, len(fail), fail

def deploy_ssl(domain):
    """
    将已有证书部署到 Nginx（更新 nginx 配置启用 HTTPS）
    v1.3.25: 从 /etc/letsencrypt/live/<domain>/ 读证书（certbot 默认位置）
    """
    le_live = f'/etc/letsencrypt/live/{domain}'
    cert_path = os.path.join(le_live, 'fullchain.pem')
    key_path = os.path.join(le_live, 'privkey.pem')

    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        return False, '证书文件不存在'

    site_path = f'/opt/tpanel/sites/{domain}/public'
    # v1.3.24: 同样查 sqlite 拿真路径
    site_path = _get_real_site_path(domain, None) or site_path

    # 写入 HTTPS + HTTP 重定向配置
    nginx_conf = f'''# {domain} - HTTPS
server {{
    listen 80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    root {site_path};
    index index.php index.html;

    access_log /opt/tpanel/logs/{domain}.access.log;
    error_log /opt/tpanel/logs/{domain}.error.log;

    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}

    location ~ \\.php$ {{
        include fastcgi_params;
        fastcgi_pass 127.0.0.1:9000;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }}

    location ~ /\\.ht {{
        deny all;
    }}
}}
'''
    conf_path = f'/etc/nginx/sites-available/{domain}.conf'

    # v1.3.34 修复：用 sudo rm 清理(前面已经会 rm -f,这里简化)

    with open(conf_path, 'w') as f:
        f.write(nginx_conf)

    # v1.3.34 修复：用 sudo ln -sf (sites-enabled 目录 root-only 可写)
    enabled_path = f'/etc/nginx/sites-enabled/{domain}.conf'
    # 先 rm 旧的(无论是 symlink 还是普通文件)
    _run(['sudo', 'rm', '-f', enabled_path])
    r = _run(['sudo', 'ln', '-sf', conf_path, enabled_path])
    if r[0] != 0:
        return False, f'创建 symlink 失败: {r[2]}'

    code, out, err = _run(['sudo', 'nginx', '-t'])
    if code != 0:
        return False, f'Nginx 配置错误: {err}'

    _run(['sudo', 'nginx', '-s', 'reload'])

    # 更新数据库 ssl_enabled + ssl_certs 表
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id FROM sites WHERE domain = ?", (domain,))
    site_row = cur.fetchone()
    site_id = site_row[0] if site_row else None
    if site_id:
        conn.execute("UPDATE sites SET ssl_enabled = 1, ssl_cert_path = ?, ssl_key_path = ? WHERE domain = ?",
                     (cert_path, key_path, domain))

    # v1.3.34 修复：必须把证书插到 ssl_certs 表（前端列表才会显示）
    info = get_cert_info(cert_path)
    expire_date = info["expire_date"] if info else ""
    cur2 = conn.execute("SELECT id FROM ssl_certs WHERE domain = ?", (domain,))
    existing = cur2.fetchone()
    if existing:
        conn.execute("UPDATE ssl_certs SET cert_path = ?, key_path = ?, expire_date = ?, auto_renew = 1, site_id = ? WHERE domain = ?",
                     (cert_path, key_path, expire_date, site_id, domain))
    else:
        conn.execute("INSERT INTO ssl_certs (site_id, domain, cert_path, key_path, expire_date, auto_renew) VALUES (?, ?, ?, ?, ?, 1)",
                     (site_id, domain, cert_path, key_path, expire_date))
    conn.commit()
    conn.close()

    return True, "HTTPS 已启用，到期 " + expire_date

def check_certs_status():
    """
    检查所有证书状态，返回统计信息
    """
    certs = get_all_certs()
    expired = []
    expiring = []
    valid = []

    for cert in certs:
        info = get_cert_info(cert['cert_path'])
        if info:
            days = info['days_left']
            if days < 0:
                expired.append({**cert, **info})
            elif days <= 7:
                expiring.append({**cert, **info})
            else:
                valid.append({**cert, **info})

    return {
        'total': len(certs),
        'valid': len(valid),
        'expiring': len(expiring),
        'expired': len(expired),
        'expiring_list': expiring,
        'expired_list': expired,
    }