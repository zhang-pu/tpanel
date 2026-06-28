"""
TPanel - 系统操作模块
仅使用白名单命令，禁止直接执行用户传入的原始 shell 字符串
"""
import subprocess
import os
import shutil
import tarfile
import datetime
import time

def _detect_pkg_manager():
    """检测系统包管理器"""
    import shutil
    for p in ['apt-get', 'yum', 'dnf']:
        if shutil.which(p):
            return p
    return None


def _run(cmd, shell=False, capture=True, timeout=30):
    """执行命令，超时保护"""
    try:
        if isinstance(cmd, str) and not shell:
            cmd = cmd.split()
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            shell=shell
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out'
    except Exception as e:
        return -1, '', str(e)

def nginx_reload():
    return _run(['sudo', 'nginx', '-t']) + _run(['sudo', 'nginx', '-s', 'reload'])

def nginx_stop():
    return _run(['sudo', 'nginx', '-s', 'stop'])

def nginx_start():
    return _run(['sudo', 'nginx'])

def nginx_status():
    code, out, _ = _run(['ps', 'aux'], capture=True)
    running = 'nginx: master' in out
    return running

def mysql_status():
    # Debian 12 默认是 mariadb，CentOS 是 mysql
    for svc in ['mariadb', 'mysql']:
        code, out, _ = _run(['systemctl', 'is-active', svc], capture=True)
        if code == 0:
            return True
    return False
    return out == 'active'

def create_site_user(username):
    """创建 Linux 用户，禁 shell，隔离目录（v1.3.11+ 改用 sudo）"""
    # 检查用户是否存在
    code, out, _ = _run(['id', username], capture=True)
    if code == 0:
        return True, '用户已存在'

    # 创建用户，home 目录即网站根目录，禁 shell
    code, out, err = _run(
        ['sudo', 'useradd', '-m', '-s', '/usr/sbin/nologin', '-d', f'/home/{username}', username]
    )
    if code != 0:
        return False, err
    return True, '用户创建成功'

def delete_site_user(username):
    code, out, _ = _run(['id', username], capture=True)
    if code != 0:
        return True, '用户不存在，跳过'

    # 把用户的所有进程 kill 掉再删
    _run(['pkill', '-u', username], capture=True)
    code, out, err = _run(['sudo', 'userdel', '-r', username])
    if code != 0:
        return False, err
    return True, '用户删除成功'

def set_site_permissions(site_path, site_user):
    """设置站点目录权限"""
    _run(['sudo', 'chown', '-R', f'{site_user}:{site_user}', site_path])
    _run(['sudo', 'chmod', '-R', '755', site_path])
    _run(['sudo', 'chmod', '-R', '700', site_path + '/storage' if os.path.exists(site_path + '/storage') else site_path])

def get_php_fpm_port(php_version):
    """
    v1.3.29: PHP 版本 → FPM 端口映射
    - 8.2 继续用 9000（向后兼容老 conf / install.sh 默认配置）
    - 其他版本: 90 + 小数点后两位（7.4→9074, 8.0→9080, 8.1→9081, 8.3→9083, 8.4→9084）
    - 带小数点的老版本（5.6→9056, 7.0→9070, 7.1→9071, 7.2→9072, 7.3→9073）
    - 解析失败的 default: 9000
    """
    pv = (php_version or '').strip()
    if pv == '8.2':
        return 9000
    try:
        parts = pv.split('.')
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return 9000 + major * 10 + minor
    except Exception:
        return 9000


def write_nginx_config(domain, site_path, php_version='8.1', ssl=False, site_type='php'):
    """写入 Nginx 配置
    v1.3.26 新增 site_type 参数：
    - 'php'（默认）：保留 PHP-FPM 反代 location
    - 'static'：不写 PHP-FPM 块（纯静态站点，不转发 *.php 到 FPM）
    v1.3.29: PHP-FPM 端口随版本变化（多版本并存不冲突）
    """
    # PHP-FPM 连接地址（v1.3.6+ 改用 TCP 避免 unix socket 问题，v1.3.29 起按版本分端口）
    fpm_port = get_php_fpm_port(php_version)
    fpm_sock = f'127.0.0.1:{fpm_port}'

    # index 顺序 + try_files fallback 随类型不同
    if site_type == 'static':
        index_line = 'index index.html;'
        try_files_line = 'try_files $uri $uri/ =404;'
        php_block = ''  # 静态站点完全不转发 .php
    else:
        index_line = 'index index.php index.html;'
        try_files_line = 'try_files $uri $uri/ /index.php?$query_string;'
        php_block = f'''
    location ~ \\.php$ {{
        include fastcgi_params;
        fastcgi_pass {fpm_sock};
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }}
'''

    nginx_conf = f'''# TPanel - {domain} ({site_type})
server {{
    listen 80;
    server_name {domain};

    root ' + site_path + ';
    {index_line}

    access_log /opt/tpanel/logs/{domain}.access.log;
    error_log /opt/tpanel/logs/{domain}.error.log;

    location /.well-known/acme-challenge/ {{
        alias {site_path}/.well-known/acme-challenge/;
        try_files $uri =404;
    }}

    # phpMyAdmin 反代（v1.3.43：自动加,任何站点都可点数据库跳 pma）
    location ^~ /pma/ {{
        proxy_pass http://127.0.0.1:8443/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }}
    location = /pma/tpanel-bridge.php {{
        proxy_pass http://127.0.0.1:8443/tpanel-bridge.php;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location / {{
        {try_files_line}
    }}
{php_block}
    location ~ /\\.ht {{
        deny all;
    }}
}}
'''
    if ssl:
        nginx_conf = nginx_conf.replace('listen 80;', '''listen 80;
    listen 443 ssl http2;''', 1)

    conf_path = f'/etc/nginx/sites-available/{domain}.conf'
    # v1.3.15+：tpanel 不可写 /etc/nginx，用 sudo tee（先写 /tmp 临时文件）
    tmp_conf = f'/tmp/tpanel_nginx_{domain}.conf'
    with open(tmp_conf, 'w') as f:
        f.write(nginx_conf)
    code, out, err = _run(['sudo', 'mv', tmp_conf, conf_path])
    if code != 0:
        return False, f'写 conf 失败: {err}'

    # 启用站点（v1.3.15+：软链在 sites-enabled 也需 sudo）
    enabled_path = f'/etc/nginx/sites-enabled/{domain}.conf'
    if os.path.exists(enabled_path):
        _run(['sudo', 'rm', '-f', enabled_path])
    _run(['sudo', 'ln', '-sf', conf_path, enabled_path])

    code, out, err = _run(['sudo', 'nginx', '-t'])
    if code != 0:
        return False, err

    _run(['sudo', 'nginx', '-s', 'reload'])
    return True, 'Nginx 配置已更新'

def remove_nginx_config(domain):
    """删除站点 Nginx 配置（v1.3.15+ 用 sudo 删）"""
    conf_path = f'/etc/nginx/sites-available/{domain}.conf'
    enabled_path = f'/etc/nginx/sites-enabled/{domain}.conf'

    if os.path.exists(enabled_path):
        _run(['sudo', 'rm', '-f', enabled_path])
    if os.path.exists(conf_path):
        _run(['sudo', 'rm', '-f', conf_path])

    _run(['sudo', 'nginx', '-s', 'reload'])

def create_mysql_db(name, db_user, db_pass):
    """创建 MySQL 数据库和用户（用 sudo 提权，避免 shell 注入）"""
    # 校验 name/user 不含特殊字符（防止 SQL 注入）
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', name) or not re.match(r'^[a-zA-Z0-9_]+$', db_user):
        return False, '数据库名/用户名只能包含字母数字下划线'

    statements = [
        f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';",
        f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{db_user}'@'localhost';",
        "FLUSH PRIVILEGES;",
    ]
    for stmt in statements:
        code, out, err = _run(['sudo', 'mysql', '-e', stmt], shell=False)
        if code != 0:
            return False, err
    return True, '数据库创建成功'

def delete_mysql_db(name, db_user):
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', name) or not re.match(r'^[a-zA-Z0-9_]+$', db_user):
        return False, '数据库名/用户名只能包含字母数字下划线'
    statements = [
        f"DROP DATABASE IF EXISTS `{name}`;",
        f"DROP USER IF EXISTS '{db_user}'@'localhost';",
        "FLUSH PRIVILEGES;",
    ]
    for stmt in statements:
        code, out, err = _run(['sudo', 'mysql', '-e', stmt], shell=False)
        if code != 0:
            return False, err
    return True, '数据库删除成功'

def get_mysql_size():
    """获取 MySQL 数据目录大小（MB）"""
    code, out, _ = _run("du -sm /var/lib/mysql 2>/dev/null || echo 0", shell=True)
    try:
        return int(out.split()[0])
    except:
        return 0

def backup_site(site_path, site_name, db_name=None, db_user=None, db_pass=None):
    """备份站点文件和数据库"""
    import traceback
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    # 清理站点名：ygbk.cn → ygbk.cn（保留点）
    safe_name = site_name.replace('/', '_')
    backup_name = f'{safe_name}_{timestamp}'
    backup_path = f'/opt/tpanel/backups/{backup_name}.tar.gz'

    # v1.3.10 修复：预检环境
    try:
        os.makedirs('/opt/tpanel/backups', exist_ok=True)
    except Exception as e:
        return False, f'无法创建 backups 目录: {e}', 0
    if not os.path.isdir(site_path):
        return False, f'站点目录不存在: ' + site_path + '', 0
    if not os.access(site_path, os.R_OK):
        return False, f'tpanel 用户无法读取 ' + site_path + '（chown 错了？ls -ld ' + site_path + ' 看看）', 0

    try:
        # 备份文件
        with tarfile.open(backup_path, 'w:gz') as tar:
            tar.add(site_path, arcname=os.path.basename(site_path))

        # 备份数据库（v1.3.10 修复：用 list 参数防注入 + sudo）
        if db_name:
            dump_path = f'/opt/tpanel/backups/{backup_name}_db.sql.gz'
            try:
                if db_user and db_pass:
                    code, out, err = _run(
                        ['sudo', 'mysqldump', '-u', db_user, f'-p{db_pass}', db_name],
                        shell=False, timeout=120
                    )
                else:
                    code, out, err = _run(['sudo', 'mysqldump', db_name], shell=False, timeout=120)
                if code == 0 and out:
                    import gzip
                    with open(dump_path, 'wb') as df:
                        df.write(gzip.compress(out.encode('utf-8') if isinstance(out, str) else out))
                    with tarfile.open(backup_path, 'a:gz') as tar:
                        tar.add(dump_path, arcname='database.sql.gz')
                    os.remove(dump_path)
            except Exception as e:
                # 数据库备份失败不阻断（文件备份可能成功）
                pass

        size = os.path.getsize(backup_path)
        return True, backup_path, size
    except PermissionError as e:
        return False, f'权限错误: {e}（tpanel 读不到 ' + site_path + '，请 chown）', 0
    except Exception as e:
        return False, f'备份异常: {type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}', 0

def restore_backup(backup_path, site_path, site_name):
    """恢复备份"""
    try:
        # v1.3.17+：先 sudo 删干净 site_path（因为可能有 root 拥有的文件，tpanel 删不掉）
        # 用 sudo 替换为临时空目录，然后再解压
        backup_site_path = site_path
        if os.path.exists(backup_site_path):
            # 移动到 .bak 路径（sudo 移）
            bak_path = backup_site_path + '.bak.' + str(int(time.time()))
            code, _, err = _run(['sudo', 'mv', backup_site_path, bak_path])
            if code != 0:
                return False, f'备份旧目录失败: {err}'

        # 解压到临时目录
        temp_dir = f'/opt/tpanel/backups/temp_{site_name}'
        os.makedirs(temp_dir, exist_ok=True)
        with tarfile.open(backup_path, 'r:gz') as tar:
            tar.extractall(temp_dir)

        # 找到网站目录内容
        items = os.listdir(temp_dir)
        src_dir = os.path.join(temp_dir, items[0]) if items else temp_dir

        # 把整个 src 目录 sudo mv 到 site_path
        code, _, err = _run(['sudo', 'mv', src_dir, backup_site_path])
        if code != 0:
            return False, f'恢复目录失败: {err}'

        # v1.3.17+：从 site_path 反推 site_user
        # /opt/tpanel/sites/zhangpu_tech/public → zhangpu_tech
        path_parts = backup_site_path.rstrip('/').split('/')
        site_user = path_parts[-1] if path_parts else site_name
        _run(['sudo', 'chown', '-R', f'{site_user}:{site_user}', backup_site_path])
        _run(['sudo', 'chmod', '-R', '755', backup_site_path])

        shutil.rmtree(temp_dir, ignore_errors=True)
        return True, '恢复成功'
    except Exception as e:
        return False, str(e)

def run_security_update():
    """执行系统安全更新"""
    code, out, err = _run(['sudo', 'apt-get', 'update'], timeout=120)
    if code != 0:
        return False, err

    # v1.3.20+：apt-get upgrade 也加 sudo（不然 Permission denied dpkg lock）
    code, out, err = _run(
        ['sudo', 'apt-get', 'upgrade', '-y', '--only-upgrade'],
        timeout=300
    )
    if code == 0:
        return True, f'安全更新完成'
    else:
        return False, err

def get_security_status():
    """获取安全状态"""
    # 可升级的安全包数量
    code, out, _ = _run(
        "apt list --upgradable 2>/dev/null | grep -c security || echo 0",
        shell=True
    )
    try:
        updatable = int(out.strip())
    except:
        updatable = 0

    # 最近的安全日志条数
    code2, out2, _ = _run(
        "journalctl --since '1 day ago' --priority=err 2>/dev/null | wc -l",
        shell=True
    )
    try:
        errors = int(out2.strip())
    except:
        errors = 0

    return {'upgradable_security_packages': updatable, 'recent_errors': errors}

def get_system_stats():
    """获取系统状态"""
    code, cpu_out, _ = _run("cat /proc/loadavg | awk '{print $1,$2,$3}'", shell=True)
    code, mem_out, _ = _run("free -m | awk 'NR==2{print $3,$2}'", shell=True)
    code, disk_out, _ = _run("df -h / | tail -1 | awk '{print $3,$4}'", shell=True)
    code, cpu_pct, _ = _run("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | sed 's/%us,//'", shell=True)

    # v1.3.10+ 新增：CPU 核心数 + 型号（用于仪表盘显示 + 负载颜色按核心数判断）
    # v1.3.35 修复：容器/Docker 里 lscpu 无 "Model name" 行会导致 Unknown CPU
    import os as _os
    cpu_cores = _os.cpu_count() or 1
    cpu_model = ''
    # 1. 优先 lscpu "Model name"（KVM/Xen 等虚拟化都正常）
    code, lscpu_out, _ = _run("lscpu | grep 'Model name' | head -1", shell=True)
    if code == 0 and lscpu_out and ':' in lscpu_out:
        cpu_model = lscpu_out.split(':', 1)[1].strip()
    # 2. 兑底：/proc/cpuinfo 的 model name（v1.3.35 修复：必传 shell=True）
    if not cpu_model:
        code, cpuinfo_out, _ = _run("grep -m1 'model name' /proc/cpuinfo", shell=True)
        if code == 0 and cpuinfo_out and ':' in cpuinfo_out:
            cpu_model = cpuinfo_out.split(':', 1)[1].strip()
    # 3. 兑底：/proc/cpuinfo 拼 vendor + family + model（容器里 lscpu 可能无 Model name）
    if not cpu_model:
        try:
            with open('/proc/cpuinfo', 'r') as f:
                ci = f.read()
            vendor = family = model_name = ''
            for line in ci.splitlines():
                if line.startswith('vendor_id') and ':' in line and not vendor:
                    vendor = line.split(':', 1)[1].strip()
                elif line.startswith('cpu family') and ':' in line and not family:
                    family = line.split(':', 1)[1].strip()
                elif line.startswith('model name') and ':' in line and not model_name:
                    model_name = line.split(':', 1)[1].strip()
                if model_name: break
            if model_name:
                cpu_model = model_name
            elif vendor:
                cpu_model = f'{vendor} CPU'
                if family: cpu_model += f' (family {family})'
        except Exception:
            pass
    # 4. 兑底：platform.processor()（老 Python 偶尔能拿到）
    if not cpu_model:
        try:
            import platform
            cpu_model = platform.processor() or ''
        except Exception:
            pass
    # 5. 兑底：lscpu 看 Vendor ID + Model（某些云主机会输出这个）
    if not cpu_model:
        code, lscpu_v, _ = _run("lscpu | grep -E 'Vendor ID|Model:' | head -2", shell=True)
        if code == 0 and lscpu_v:
            parts = []
            for line in lscpu_v.strip().splitlines():
                if ':' in line:
                    parts.append(line.split(':', 1)[1].strip())
            if parts:
                cpu_model = ' '.join(parts) + ' CPU'
    if not cpu_model:
        cpu_model = 'Unknown CPU'

    nginx_running = nginx_status()
    mysql_running = mysql_status()

    return {
        'load': cpu_out,
        'cpu_pct': cpu_pct.strip() + '%' if cpu_pct else 'N/A',
        'cpu_cores': cpu_cores,
        'cpu_model': cpu_model,
        'mem_used_mb': mem_out.split()[0] if mem_out else '0',
        'mem_total_mb': mem_out.split()[1] if mem_out else '0',
        'disk_used': disk_out.split()[0] if disk_out else '0',
        'disk_free': disk_out.split()[1] if disk_out else '0',
        'nginx_running': nginx_running,
        'mysql_running': mysql_running,
    }

def write_log(event_type, details, ip=''):
    """写安全日志"""
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO security_logs (event_type, details, ip) VALUES (?, ?, ?)",
                 (event_type, details, ip))
    conn.commit()
    conn.close()


def setup_php_fpm_listen(php_version):
    """
    v1.3.29: 装完 PHP 后调用——设置 FPM listen 端口为版本专属端口，并启动服务
    - 写 /etc/php/<ver>/fpm/pool.d/www.conf（备份原文件为 .bak）
    - sudo systemctl enable --now php<ver>-fpm
    返回: (ok, msg)
    """
    port = get_php_fpm_port(php_version)
    www_conf = f'/etc/php/{php_version}/fpm/pool.d/www.conf'
    if not os.path.exists(www_conf):
        return False, f'找不到 {www_conf}（该版本未安装？）'

    # 备份（幂等：不重复备份）
    bak = www_conf + '.tpanel.bak'
    if not os.path.exists(bak):
        code, _, err = _run(['sudo', 'cp', www_conf, bak])
        if code != 0:
            return False, f'备份 {www_conf} 失败: {err}'

    # 修改 listen 行（用 sed 精准替换）
    code, _, err = _run(['sudo', 'bash', '-c',
        f"sed -i 's|^listen = .*|listen = 127.0.0.1:{port}|' {www_conf}"])
    if code != 0:
        return False, f'修改 listen 失败: {err}'

    # 启用 + 启动
    code, _, err = _run(['sudo', 'systemctl', 'enable', f'php{php_version}-fpm'])
    if code != 0:
        return False, f'enable php{php_version}-fpm 失败: {err}'

    code, out, err = _run(['sudo', 'systemctl', 'restart', f'php{php_version}-fpm'])
    if code != 0:
        return False, f'restart php{php_version}-fpm 失败: {err}'

    # 验证在监听
    code, out, _ = _run(['sudo', 'ss', '-lntp'])
    listening = f'127.0.0.1:{port}' in out
    if not listening:
        return False, f'php{php_version}-fpm 未在 127.0.0.1:{port} 监听（可能启动失败）'

    return True, f'php{php_version}-fpm 已配置 listen 127.0.0.1:{port} 并启动'

def change_db_password(db_user, new_pass):
    """修改 MySQL 数据库用户密码（v1.3.34+）"""
    import re
    if not re.match(r"^[a-zA-Z0-9_]+$", db_user):
        return False, "用户名只能包含字母数字下划线"
    if not new_pass or len(new_pass) < 6:
        return False, "密码至少 6 位"
    escaped_pass = new_pass.replace("'", "''")
    stmt = "ALTER USER '" + db_user + "'@'localhost' IDENTIFIED BY '" + escaped_pass + "';"
    code, out, err = _run(["sudo", "mysql", "-e", stmt], shell=False)
    if code != 0:
        return False, err
    code, _, err = _run(["sudo", "mysql", "-e", "FLUSH PRIVILEGES;"], shell=False)
    if code != 0:
        return False, err
    return True, "密码修改成功"


# ====================== v1.3.37+ 生产增强功能 ======================

# ---------------------- 防火墙管理 ----------------------

def _detect_firewall():
    """检测系统使用的防火墙：ufw (Debian/Ubuntu) 或 firewalld (CentOS/RHEL)"""
    if shutil.which('ufw'):
        return 'ufw'
    if shutil.which('firewall-cmd'):
        return 'firewalld'
    return None

def get_firewall_status():
    """获取防火墙状态和已开放端口"""
    fw = _detect_firewall()
    if not fw:
        return {'enabled': False, 'type': None, 'rules': [], 'msg': '未检测到防火墙（ufw/firewalld）'}

    if fw == 'ufw':
        code, status, _ = _run(['sudo', 'ufw', 'status'])
        enabled = 'Status: active' in status
        # 解析规则
        lines = status.split('\n')
        rules = []
        in_rules = False
        for line in lines:
            if '----' in line:
                in_rules = True
                continue
            if in_rules and line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    rules.append({
                        'port': parts[0],
                        'action': parts[1],
                        'from': parts[2] if len(parts) > 2 else 'Anywhere'
                    })
        return {'enabled': enabled, 'type': 'ufw', 'rules': rules, 'status': status}

    else:  # firewalld
        code, status, _ = _run(['sudo', 'firewall-cmd', '--state'])
        enabled = status.strip() == 'running'
        code, ports, _ = _run(['sudo', 'firewall-cmd', '--list-ports'])
        rules = [{'port': p, 'action': 'allow', 'from': 'public'} for p in ports.split() if p.strip()]
        return {'enabled': enabled, 'type': 'firewalld', 'rules': rules, 'status': status}

def firewall_enable():
    """启用防火墙并开放常用端口（SSH 80 443 + TPanel 端口）"""
    fw = _detect_firewall()
    if not fw:
        # 自动安装 ufw
        pkg = _detect_pkg_manager()
        if pkg == 'apt-get':
            code, _, err = _run(['sudo', 'apt-get', 'install', '-y', 'ufw'], timeout=120)
        elif pkg in ['yum', 'dnf']:
            code, _, err = _run(['sudo', pkg, 'install', '-y', 'firewalld'], timeout=120)
        else:
            return False, '不支持的系统包管理器'
        if code != 0:
            return False, f'安装防火墙失败: {err}'
        fw = _detect_firewall()

    if fw == 'ufw':
        # 默认策略
        _run(['sudo', 'ufw', 'default', 'deny', 'incoming'])
        _run(['sudo', 'ufw', 'default', 'allow', 'outgoing'])
        # 开放常用端口
        for port in ['22', '80', '443', '8888']:
            _run(['sudo', 'ufw', 'allow', port])
        # 启用
        code, _, err = _run(['sudo', 'bash', '-c', 'echo "y" | ufw enable'], shell=True)
        return code == 0, '防火墙已启用，已开放 22/80/443/8888 端口'

    else:  # firewalld
        _run(['sudo', 'systemctl', 'enable', '--now', 'firewalld'])
        for port in ['22/tcp', '80/tcp', '443/tcp', '8888/tcp']:
            _run(['sudo', 'firewall-cmd', '--permanent', '--add-port=' + port])
        _run(['sudo', 'firewall-cmd', '--reload'])
        return True, '防火墙已启用，已开放 22/80/443/8888 端口'

def firewall_open_port(port, proto='tcp'):
    """开放端口"""
    if not port.isdigit() or int(port) < 1 or int(port) > 65535:
        return False, '端口号无效（1-65535）'

    fw = _detect_firewall()
    if not fw:
        return False, '未检测到防火墙，请先启用'

    if fw == 'ufw':
        code, _, err = _run(['sudo', 'ufw', 'allow', f'{port}/{proto}'])
        return code == 0, f'端口 {port}/{proto} 已开放'
    else:
        _run(['sudo', 'firewall-cmd', '--permanent', f'--add-port={port}/{proto}'])
        _run(['sudo', 'firewall-cmd', '--reload'])
        return True, f'端口 {port}/{proto} 已开放'

def firewall_close_port(port, proto='tcp'):
    """关闭端口"""
    if not port.isdigit() or int(port) < 1 or int(port) > 65535:
        return False, '端口号无效'

    fw = _detect_firewall()
    if not fw:
        return False, '未检测到防火墙'

    if fw == 'ufw':
        code, _, err = _run(['sudo', 'ufw', 'delete', 'allow', f'{port}/{proto}'])
        return code == 0, f'端口 {port}/{proto} 已关闭'
    else:
        _run(['sudo', 'firewall-cmd', '--permanent', f'--remove-port={port}/{proto}'])
        _run(['sudo', 'firewall-cmd', '--reload'])
        return True, f'端口 {port}/{proto} 已关闭'


# ---------------------- 面板端口修改 ----------------------

def get_panel_port():
    """获取当前 TPanel 监听端口"""
    # 先从 Nginx 配置查
    code, out, _ = _run(['sudo', 'grep', '-r', 'listen', '/etc/nginx/sites-enabled/tpanel.conf'])
    if code == 0:
        for line in out.split('\n'):
            if 'listen ' in line and 'default_server' not in line:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    # 查 systemd 服务
    code, out, _ = _run(['grep', 'ExecStart', '/etc/systemd/system/tpanel.service'])
    if code == 0 and '--port' in out:
        idx = out.find('--port')
        port_part = out[idx:].split()[1]
        if port_part.isdigit():
            return int(port_part)
    return 8888  # 默认

def change_panel_port(new_port):
    """修改 TPanel 后台端口
    1. 修改 Nginx 反向代理配置（8888 -> new_port）
    2. 修改 systemd 服务启动端口
    3. 防火墙开放新端口
    4. 重启服务生效
    """
    if not str(new_port).isdigit() or int(new_port) < 1000 or int(new_port) > 65535:
        return False, '端口号无效（1000-65535）'
    new_port = int(new_port)
    old_port = get_panel_port()

    if old_port == new_port:
        return False, '新端口与当前端口相同'

    # 1. 防火墙开放新端口
    fw = _detect_firewall()
    if fw and get_firewall_status()['enabled']:
        firewall_open_port(str(new_port))

    # 2. 修改 Nginx 配置
    nginx_conf = '/etc/nginx/sites-enabled/tpanel.conf'
    if os.path.exists(nginx_conf):
        code, _, err = _run(['sudo', 'sed', '-i', f's/proxy_pass http:\/\/127.0.0.1:{old_port}/proxy_pass http://127.0.0.1:{new_port}/', nginx_conf])
        if code != 0:
            return False, f'修改 Nginx 配置失败: {err}'
        # 修改 listen 端口
        _run(['sudo', 'sed', '-i', f's/listen {old_port}/listen {new_port}/', nginx_conf])

    # 3. 修改 systemd 服务
    service_file = '/etc/systemd/system/tpanel.service'
    if os.path.exists(service_file):
        code, _, err = _run(['sudo', 'sed', '-i', f's/--port {old_port}/--port {new_port}/', service_file])
        if code != 0:
            return False, f'修改 systemd 服务失败: {err}'

    # 4. 重新加载 daemon 并重启服务
    _run(['sudo', 'systemctl', 'daemon-reload'])
    code, _, err = _run(['sudo', 'systemctl', 'restart', 'tpanel'])
    if code != 0:
        return False, f'重启 TPanel 服务失败: {err}'

    # 5. 重启 Nginx
    _run(['sudo', 'nginx', '-s', 'reload'])

    # 6. 关闭旧端口防火墙（如果之前开着）
    if fw and get_firewall_status()['enabled']:
        firewall_close_port(str(old_port))

    return True, f'端口修改成功！新端口: {new_port}，请刷新页面重新访问'


# ---------------------- 自动备份 ----------------------

def get_backup_settings():
    """获取备份配置状态"""
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM backup_settings LIMIT 1")
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(zip(cols, row))
    return {'enabled': 0, 'schedule': 'daily', 'keep_days': 7, 'backup_dir': '/backup'}

def save_backup_settings(enabled, schedule, keep_days, backup_dir='/backup'):
    """保存自动备份配置"""
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)

    # 检查表是否存在
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='backup_settings'")
    if not cur.fetchone():
        conn.execute("""
            CREATE TABLE backup_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enabled INTEGER DEFAULT 0,
                schedule TEXT DEFAULT 'daily',
                keep_days INTEGER DEFAULT 7,
                backup_dir TEXT DEFAULT '/backup',
                updated_at TEXT
            )
        """)

    conn.execute("DELETE FROM backup_settings")
    conn.execute("""
        INSERT INTO backup_settings (enabled, schedule, keep_days, backup_dir, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (1 if enabled else 0, schedule, keep_days, backup_dir, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()

    # 配置 cron 定时任务
    if enabled:
        if schedule == 'daily':
            cron_expr = '0 3 * * *'  # 每天凌晨3点
        elif schedule == 'weekly':
            cron_expr = '0 2 * * 0'  # 每周日凌晨2点
        else:  # hourly
            cron_expr = '0 * * * *'  # 每小时

        # 创建备份脚本
        script_content = f'''#!/bin/bash
# TPanel 自动备份脚本
BACKUP_DIR="{backup_dir}"
KEEP_DAYS={keep_days}
DATE=$(date +%Y%m%d_%H%M%S)

# 创建备份目录
mkdir -p $BACKUP_DIR/sites
mkdir -p $BACKUP_DIR/databases

# 备份所有站点
for site_user in /opt/tpanel/sites/*/; do
    site_name=$(basename "$site_user")
    tar -czf "$BACKUP_DIR/sites/${site_name}_${DATE}.tar.gz" -C /opt/tpanel/sites "$site_name" 2>/dev/null
done

# 备份所有数据库
for db in $(sudo mysql -e "SHOW DATABASES;" | grep -vE "(Database|information_schema|performance_schema|mysql|sys)"); do
    sudo mysqldump "$db" > "$BACKUP_DIR/databases/${db}_${DATE}.sql" 2>/dev/null
    gzip -f "$BACKUP_DIR/databases/${db}_${DATE}.sql"
done

# 清理过期备份
find "$BACKUP_DIR/sites" -name "*.tar.gz" -mtime +$KEEP_DAYS -delete
find "$BACKUP_DIR/databases" -name "*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "Backup completed at $(date)" >> /opt/tpanel/logs/backup.log
'''
        os.makedirs('/opt/tpanel/scripts', exist_ok=True)
        with open('/opt/tpanel/scripts/auto_backup.sh', 'w') as f:
            f.write(script_content)
        os.chmod('/opt/tpanel/scripts/auto_backup.sh', 0o755)

        # 添加到 crontab
        code, out, _ = _run('crontab -l 2>/dev/null || echo ""', shell=True)
        lines = [l for l in out.split('\n') if 'auto_backup.sh' not in l and l.strip()]
        lines.append(f'{cron_expr} /opt/tpanel/scripts/auto_backup.sh')
        _run(f'echo "{chr(10).join(lines)}" | crontab -', shell=True)

    else:
        # 禁用：从 crontab 移除
        code, out, _ = _run('crontab -l 2>/dev/null || echo ""', shell=True)
        if code == 0:
            lines = [l for l in out.split('\n') if 'auto_backup.sh' not in l and l.strip()]
            _run(f'echo "{chr(10).join(lines)}" | crontab -', shell=True)

    return True, '备份配置已保存'

def run_backup_now():
    """立即执行一次手动备份"""
    script = '/opt/tpanel/scripts/auto_backup.sh'
    if not os.path.exists(script):
        return False, '备份脚本不存在，请先配置自动备份'

    code, out, err = _run(['sudo', 'bash', script], timeout=300)
    return code == 0, out if code == 0 else err

def list_backups():
    """列出所有备份文件"""
    settings = get_backup_settings()
    backup_dir = settings['backup_dir']

    if not os.path.exists(backup_dir):
        return {'sites': [], 'databases': [], 'total_size': '0 MB'}

    sites = []
    sites_dir = os.path.join(backup_dir, 'sites')
    if os.path.exists(sites_dir):
        for f in sorted(os.listdir(sites_dir), reverse=True):
            path = os.path.join(sites_dir, f)
            if os.path.isfile(path):
                size_mb = round(os.path.getsize(path) / 1024 / 1024, 2)
                sites.append({'name': f, 'size': f'{size_mb} MB', 'path': path, 'mtime': datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat()})

    dbs = []
    dbs_dir = os.path.join(backup_dir, 'databases')
    if os.path.exists(dbs_dir):
        for f in sorted(os.listdir(dbs_dir), reverse=True):
            path = os.path.join(dbs_dir, f)
            if os.path.isfile(path):
                size_mb = round(os.path.getsize(path) / 1024 / 1024, 2)
                dbs.append({'name': f, 'size': f'{size_mb} MB', 'path': path, 'mtime': datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat()})

    # 计算总大小
    total_size = sum(float(s['size'].split()[0]) for s in sites + dbs)

    return {
        'sites': sites,
        'databases': dbs,
        'total_size': f'{round(total_size, 2)} MB',
        'backup_dir': backup_dir
    }

def delete_backup(backup_type, filename):
    """删除备份文件"""
    settings = get_backup_settings()
    if backup_type not in ['sites', 'databases']:
        return False, '类型无效'

    path = os.path.join(settings['backup_dir'], backup_type, filename)
    if not os.path.exists(path):
        return False, '文件不存在'

    try:
        os.remove(path)
        return True, '备份已删除'
    except Exception as e:
        return False, str(e)


# ====================== v1.3.39+ 在线升级功能 ======================

def get_server_info():
    """Get server info: IP / hostname / OS / kernel / uptime / public IP / panel version"""
    import socket
    import platform

    info = {
        "internal_ip": "",
        "hostname": "",
        "os": "",
        "kernel": "",
        "uptime": "",
        "public_ip": "",
        "panel_version": "",
    }

    # 1. Internal IP (multiple fallbacks)
    # 1a. hostname -I (fastest)
    code, out, _ = _run(["hostname", "-I"], shell=False)
    if code == 0 and out:
        info["internal_ip"] = out.strip().split()[0]
    # 1b. Fallback: ip route get 1.1.1.1
    if not info["internal_ip"]:
        code, out, _ = _run(["bash", "-c", "ip route get 1.1.1.1 2>/dev/null | awk -F'src' '{print $2}' | awk '{print $1}'"], shell=False)
        if code == 0 and out:
            info["internal_ip"] = out.strip()
    # 1c. Fallback: UDP socket
    if not info["internal_ip"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("1.1.1.1", 80))
            info["internal_ip"] = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    # 2. Hostname
    try:
        info["hostname"] = socket.gethostname()
    except Exception:
        pass

    # 3. OS (try /etc/os-release PRETTY_NAME first)
    code, out, _ = _run(["bash", "-c", "grep PRETTY_NAME /etc/os-release 2>/dev/null | head -1 | cut -d= -f2 | tr -d '\"'"], shell=False)
    if code == 0 and out:
        info["os"] = out.strip()
    if not info["os"]:
        info["os"] = platform.platform()

    # 4. Kernel
    info["kernel"] = platform.release()

    # 5. Uptime (human-readable)
    code, out, _ = _run(["uptime", "-p"], shell=False)
    if code == 0 and out:
        info["uptime"] = out.strip()

    # 6. Public IP (ipify, 3s timeout; fall back to ip.cn)
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "TPanel/" + get_current_version()})
        with urllib.request.urlopen(req, timeout=3) as resp:
            j = _json.loads(resp.read().decode("utf-8"))
            info["public_ip"] = j.get("ip", "")
    except Exception:
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request("https://ip.cn/api/index?ip=&type=0", headers={"User-Agent": "curl/7"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                j = _json.loads(resp.read().decode("utf-8"))
                info["public_ip"] = j.get("ip", "")
        except Exception:
            pass

    # 7. Panel version
    info["panel_version"] = get_current_version()

    return info


def get_server_info():
    """Get server info: IP / hostname / OS / kernel / uptime / public IP / panel version"""
    import socket
    import platform

    info = {
        "internal_ip": "",
        "hostname": "",
        "os": "",
        "kernel": "",
        "uptime": "",
        "public_ip": "",
        "panel_version": "",
    }

    # 1. Internal IP (multiple fallbacks)
    # 1a. hostname -I (fastest)
    code, out, _ = _run(["hostname", "-I"], shell=False)
    if code == 0 and out:
        info["internal_ip"] = out.strip().split()[0]
    # 1b. Fallback: ip route get 1.1.1.1
    if not info["internal_ip"]:
        code, out, _ = _run(["bash", "-c", "ip route get 1.1.1.1 2>/dev/null | awk -F'src' '{print $2}' | awk '{print $1}'"], shell=False)
        if code == 0 and out:
            info["internal_ip"] = out.strip()
    # 1c. Fallback: UDP socket
    if not info["internal_ip"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("1.1.1.1", 80))
            info["internal_ip"] = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    # 2. Hostname
    try:
        info["hostname"] = socket.gethostname()
    except Exception:
        pass

    # 3. OS (try /etc/os-release PRETTY_NAME first)
    code, out, _ = _run(["bash", "-c", "grep PRETTY_NAME /etc/os-release 2>/dev/null | head -1 | cut -d= -f2 | tr -d '\"'"], shell=False)
    if code == 0 and out:
        info["os"] = out.strip()
    if not info["os"]:
        info["os"] = platform.platform()

    # 4. Kernel
    info["kernel"] = platform.release()

    # 5. Uptime (human-readable)
    code, out, _ = _run(["uptime", "-p"], shell=False)
    if code == 0 and out:
        info["uptime"] = out.strip()

    # 6. Public IP (ipify, 3s timeout; fall back to ip.cn)
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": "TPanel/" + get_current_version()})
        with urllib.request.urlopen(req, timeout=3) as resp:
            j = _json.loads(resp.read().decode("utf-8"))
            info["public_ip"] = j.get("ip", "")
    except Exception:
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request("https://ip.cn/api/index?ip=&type=0", headers={"User-Agent": "curl/7"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                j = _json.loads(resp.read().decode("utf-8"))
                info["public_ip"] = j.get("ip", "")
        except Exception:
            pass

    # 7. Panel version
    info["panel_version"] = get_current_version()

    return info


def get_current_version():
    """获取当前版本号（从前端HTML里提取）"""
    index_path = '/opt/tpanel/frontend/index.html'
    if not os.path.exists(index_path):
        return '1.0.0'
    try:
        with open(index_path, 'r') as f:
            content = f.read()
        import re
        m = re.search(r'T面板 v([\d.]+)', content)
        return m.group(1) if m else '1.0.0'
    except Exception:
        return '1.0.0'

def check_latest_version():
    """检测最新版本（GitHub API / 官方CDN）"""
    # TODO: 以后有官方域名后换成真实地址，现在先返回本地版本
    # 临时方案：返回当前版本 + 提示功能已就绪
    return {
        'current': get_current_version(),
        'latest': get_current_version(),
        'has_update': False,
        'download_url': '',
        'release_notes': '在线升级功能已就绪，支持手动上传安装包升级'
    }

def backup_current_version():
    """升级前自动备份当前版本"""
    import shutil
    import datetime
    version = get_current_version()
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = f'/opt/tpanel/backup_v{version}_{timestamp}.zip'
    
    try:
        # 备份 backend 和 frontend 目录
        with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk('/opt/tpanel/backend'):
                for file in files:
                    if not file.endswith('.pyc') and '__pycache__' not in root:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, '/opt/tpanel')
                        zf.write(full_path, arcname)
            for root, dirs, files in os.walk('/opt/tpanel/frontend'):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, '/opt/tpanel')
                    zf.write(full_path, arcname)
        return True, backup_file
    except Exception as e:
        return False, str(e)

def upgrade_from_zip(zip_path):
    """从上传的zip包升级"""
    import zipfile
    import shutil
    
    # 1. 先备份当前版本
    ok, backup_file = backup_current_version()
    if not ok:
        return False, f'备份失败: {backup_file}'
    
    # 2. 验证zip包
    if not os.path.exists(zip_path):
        return False, '安装包不存在'
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # 检查必须的目录
            names = zf.namelist()
            has_backend = any('backend/' in n for n in names)
            has_frontend = any('frontend/' in n for n in names)
            if not has_backend or not has_frontend:
                return False, '安装包格式错误：缺少 backend 或 frontend 目录'
    except Exception as e:
        return False, f'安装包损坏: {str(e)}'
    
    # 3. 解压覆盖
    try:
        temp_dir = '/tmp/tpanel_upgrade'
        shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)
        
        # 覆盖文件
        if os.path.exists(f'{temp_dir}/backend'):
            _run(['sudo', 'cp', '-rf', f'{temp_dir}/backend/', '/opt/tpanel/'])
        if os.path.exists(f'{temp_dir}/frontend'):
            _run(['sudo', 'cp', '-rf', f'{temp_dir}/frontend/', '/opt/tpanel/'])
        
        # 清理临时文件
        shutil.rmtree(temp_dir, ignore_errors=True)
        os.remove(zip_path)
        
        return True, f'升级成功！已备份旧版本到: {backup_file}'
    except Exception as e:
        return False, f'升级失败: {str(e)}'

def rollback_version(backup_file):
    """回滚到指定备份版本"""
    import zipfile
    if not os.path.exists(backup_file):
        return False, '备份文件不存在'
    
    try:
        temp_dir = '/tmp/tpanel_rollback'
        shutil.rmtree(temp_dir, ignore_errors=True)
        os.makedirs(temp_dir, exist_ok=True)
        
        with zipfile.ZipFile(backup_file, 'r') as zf:
            zf.extractall(temp_dir)
        
        _run(['sudo', 'cp', '-rf', f'{temp_dir}/backend/', '/opt/tpanel/'])
        _run(['sudo', 'cp', '-rf', f'{temp_dir}/frontend/', '/opt/tpanel/'])
        
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True, '回滚成功'
    except Exception as e:
        return False, f'回滚失败: {str(e)}'

def list_backup_versions():
    """列出所有可回滚的版本备份"""
    backups = []
    try:
        for f in os.listdir('/opt/tpanel'):
            if f.startswith('backup_v') and f.endswith('.zip'):
                stat = os.stat(f'/opt/tpanel/{f}')
                size_mb = round(stat.st_size / 1024 / 1024, 2)
                backups.append({
                    'name': f,
                    'path': f'/opt/tpanel/{f}',
                    'size': f'{size_mb} MB',
                    'mtime': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
        return sorted(backups, key=lambda x: x['mtime'], reverse=True)
    except Exception:
        return []
