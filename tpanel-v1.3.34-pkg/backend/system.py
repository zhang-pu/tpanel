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

    root {site_path};
    {index_line}

    access_log /opt/tpanel/logs/{domain}.access.log;
    error_log /opt/tpanel/logs/{domain}.error.log;

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
        return False, f'站点目录不存在: {site_path}', 0
    if not os.access(site_path, os.R_OK):
        return False, f'tpanel 用户无法读取 {site_path}（chown 错了？ls -ld {site_path} 看看）', 0

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
        return False, f'权限错误: {e}（tpanel 读不到 {site_path}，请 chown）', 0
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
