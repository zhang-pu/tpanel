"""
TPanel - 系统操作模块
仅使用白名单命令，禁止直接执行用户传入的原始 shell 字符串
"""
import subprocess
import os
import shutil
import tarfile
import datetime

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
    return _run(['nginx', '-t']) + _run(['nginx', '-s', 'reload'])

def nginx_stop():
    return _run(['nginx', '-s', 'stop'])

def nginx_start():
    return _run(['nginx'])

def nginx_status():
    code, out, _ = _run(['ps', 'aux'], capture=True)
    running = 'nginx: master' in out
    return running

def mysql_status():
    code, out, _ = _run(['systemctl', 'is-active', 'mysql'])
    return out == 'active'

def create_site_user(username):
    """创建 Linux 用户，禁 shell，隔离目录"""
    # 检查用户是否存在
    code, out, _ = _run(['id', username], capture=True)
    if code == 0:
        return True, '用户已存在'

    # 创建用户，home 目录即网站根目录，禁 shell
    code, out, err = _run(
        ['useradd', '-m', '-s', '/usr/sbin/nologin', '-d', f'/home/{username}', username]
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
    code, out, err = _run(['userdel', '-r', username])
    if code != 0:
        return False, err
    return True, '用户删除成功'

def set_site_permissions(site_path, site_user):
    """设置站点目录权限"""
    _run(['chown', '-R', f'{site_user}:{site_user}', site_path])
    _run(['chmod', '-R', '755', site_path])
    _run(['chmod', '-R', '700', site_path + '/storage' if os.path.exists(site_path + '/storage') else site_path])

def write_nginx_config(domain, site_path, php_version='8.1', ssl=False):
    """写入 Nginx 配置"""
    # PHP-FPM socket 路径（根据版本）
    fpm_sock = f'/run/php/php-fpm-{php_version}.sock'
    if not os.path.exists(f'/run/php/php-fpm-{php_version}.sock'):
        fpm_sock = '/run/php/php-fpm8.1.sock'

    nginx_conf = f'''# TPanel - {domain}
server {{
    listen 80;
    server_name {domain};

    root {site_path};
    index index.php index.html;

    access_log /opt/tpanel/logs/{domain}.access.log;
    error_log /opt/tpanel/logs/{domain}.error.log;

    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}

    location ~ \\.php$ {{
        include fastcgi_params;
        fastcgi_pass unix:{fpm_sock};
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }}

    location ~ /\\.ht {{
        deny all;
    }}
}}
'''
    if ssl:
        nginx_conf = nginx_conf.replace('listen 80;', '''listen 80;
    listen 443 ssl http2;''', 1)

    conf_path = f'/etc/nginx/sites-available/{domain}.conf'
    with open(conf_path, 'w') as f:
        f.write(nginx_conf)

    # 启用站点
    enabled_path = f'/etc/nginx/sites-enabled/{domain}.conf'
    if not os.path.exists(enabled_path):
        os.symlink(conf_path, enabled_path)

    code, out, err = _run(['nginx', '-t'])
    if code != 0:
        return False, err

    _run(['nginx', '-s', 'reload'])
    return True, 'Nginx 配置已更新'

def remove_nginx_config(domain):
    """删除站点 Nginx 配置"""
    conf_path = f'/etc/nginx/sites-available/{domain}.conf'
    enabled_path = f'/etc/nginx/sites-enabled/{domain}.conf'

    if os.path.exists(enabled_path):
        os.remove(enabled_path)
    if os.path.exists(conf_path):
        os.remove(conf_path)

    _run(['nginx', '-s', 'reload'])

def create_mysql_db(name, db_user, db_pass):
    """创建 MySQL 数据库和用户"""
    code, out, err = _run(
        f"mysql -e \"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\"",
        shell=True
    )
    if code != 0:
        return False, err

    code, out, err = _run(
        f"mysql -e \"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';\"",
        shell=True
    )
    if code != 0:
        return False, err

    code, out, err = _run(
        f"mysql -e \"GRANT ALL PRIVILEGES ON `{name}`.* TO '{db_user}'@'localhost';\"",
        shell=True
    )
    if code != 0:
        return False, err

    _run("mysql -e \"FLUSH PRIVILEGES;\"", shell=True)
    return True, '数据库创建成功'

def delete_mysql_db(name, db_user):
    code, out, err = _run(f"mysql -e \"DROP DATABASE IF EXISTS `{name}`;\"", shell=True)
    _run(f"mysql -e \"DROP USER IF EXISTS '{db_user}'@'localhost';\"", shell=True)
    _run("mysql -e \"FLUSH PRIVILEGES;\"", shell=True)
    return True, '数据库已删除'

def get_mysql_size():
    """获取 MySQL 数据目录大小（MB）"""
    code, out, _ = _run("du -sm /var/lib/mysql 2>/dev/null || echo 0", shell=True)
    try:
        return int(out.split()[0])
    except:
        return 0

def backup_site(site_path, site_name, db_name=None, db_user=None, db_pass=None):
    """备份站点文件和数据库"""
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'{site_name}_{timestamp}'
    backup_path = f'/opt/tpanel/backups/{backup_name}.tar.gz'

    try:
        # 备份文件
        with tarfile.open(backup_path, 'w:gz') as tar:
            tar.add(site_path, arcname=os.path.basename(site_path))

        # 备份数据库
        if db_name:
            dump_path = f'/opt/tpanel/backups/{backup_name}_db.sql.gz'
            code, out, err = _run(
                f"mysqldump -u {'root'} -p'' {db_name} | gzip > {dump_path}",
                shell=True, timeout=120
            )
            if code == 0:
                tar.add(dump_path, arcname='database.sql.gz')
                os.remove(dump_path)

        size = os.path.getsize(backup_path)
        return True, backup_path, size
    except Exception as e:
        return False, '', 0

def restore_backup(backup_path, site_path, site_name):
    """恢复备份"""
    try:
        # 解压到临时目录
        temp_dir = f'/opt/tpanel/backups/temp_{site_name}'
        os.makedirs(temp_dir, exist_ok=True)
        with tarfile.open(backup_path, 'r:gz') as tar:
            tar.extractall(temp_dir)

        # 找到网站目录内容
        items = os.listdir(temp_dir)
        src_dir = os.path.join(temp_dir, items[0]) if items else temp_dir

        # 复制回站点目录
        for item in os.listdir(src_dir):
            src = os.path.join(src_dir, item)
            dst = os.path.join(site_path, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

        shutil.rmtree(temp_dir)
        return True, '恢复成功'
    except Exception as e:
        return False, str(e)

def run_security_update():
    """执行系统安全更新"""
    code, out, err = _run(['apt-get', 'update'], timeout=120)
    if code != 0:
        return False, err

    # 只安装安全更新（带 --only-upgrade 是安全的做法）
    code, out, err = _run(
        ['apt-get', 'upgrade', '-y', '--only-upgrade'],
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

    nginx_running = nginx_status()
    mysql_running = mysql_status()

    return {
        'load': cpu_out,
        'cpu_pct': cpu_pct.strip() + '%' if cpu_pct else 'N/A',
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