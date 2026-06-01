"""
TPanel - 远程备份管理（rsync）
"""
import os
import sqlite3
import subprocess
import datetime
from config import DB_PATH

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

def test_rsync_connection(host, port, user, key_path):
    """测试到远程服务器的 rsync 连接"""
    if not host or not user:
        return False, '主机和用户名不能为空'

    extra = ''
    if port and str(port) != '22':
        extra = f'-e "ssh -p {port}"'

    key = f'-i {key_path}' if key_path else ''
    cmd = f'ssh -o StrictHostKeyChecking=no {key} {user}@{host} "echo ok" {extra}'

    code, out, err = _run(cmd, timeout=15, shell=True)

    if code == 0 and 'ok' in out:
        return True, '连接成功'
    else:
        return False, err or '连接失败'

def get_remote_backups(site_id):
    """获取某站点的远程备份列表（通过 rsync 列出远程目录）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT s.site_user FROM sites s WHERE s.id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return [], '站点不存在'

    site_user = row[0]
    remote_bak_dir = f'/opt/tpanel/backups/{site_user}/'

    # 尝试通过 SSH 查看远程备份（需要配置）
    # 这里返回空列表，实际使用时由用户配置远程路径
    return [], '请配置远程备份服务器'

def run_remote_backup(site_id, remote_host, remote_user, remote_port, remote_path, key_path=None, use_password=False, password=None):
    """
    执行远程 rsync 备份
    流程：
    1. 打包本地站点文件
    2. rsync 推送到远程
    3. 记录备份日志
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_user, domain FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, '站点不存在'

    site_user, domain = row
    site_path = f'/opt/tpanel/sites/{site_user}/'

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    tar_name = f'{domain}_{timestamp}.tar.gz'
    local_tar = f'/opt/tpanel/backups/{tar_name}'

    # 1. 打包本地文件
    try:
        import tarfile
        with tarfile.open(local_tar, 'w:gz') as tar:
            tar.add(site_path, arcname=os.path.basename(site_path))

        tar_size = os.path.getsize(local_tar)
    except Exception as e:
        return False, f'打包失败: {str(e)}'

    # 2. 构建 rsync 命令
    ssh_cmd = f'ssh -o StrictHostKeyChecking=no -p {remote_port or 22}'
    if key_path and os.path.exists(key_path):
        ssh_cmd += f' -i {key_path}'

    rsync_cmd = [
        'rsync', '-avz', '--progress',
        '-e', ssh_cmd,
        local_tar,
        f'{remote_user}@{remote_host}:{remote_path}/{tar_name}'
    ]

    code, out, err = _run(rsync_cmd, timeout=600)

    # 删除本地 tar 包（节省空间）
    try:
        os.remove(local_tar)
    except:
        pass

    if code != 0:
        return False, f'rsync 失败: {err}'

    # 3. 写入备份记录
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT INTO backups (site_id, type, file_path, size, status)
                     VALUES (?, ?, ?, ?, ?)""",
                 (site_id, 'remote', f'{remote_host}:{remote_path}/{tar_name}', tar_size, 'success'))
    conn.commit()
    conn.close()

    # 4. 写安全日志
    from system import write_log
    write_log('remote_backup', f'远程备份 {domain} -> {remote_host}', '')

    return True, f'备份成功，已推送至 {remote_host}'

def sync_restore(backup_id, remote_host, remote_user, remote_port, remote_path, key_path=None):
    """
    从远程恢复备份到本地
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_id, file_path FROM backups WHERE id = ?", (backup_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, '备份记录不存在'

    site_id, remote_file = row

    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.execute("SELECT site_user, domain FROM sites WHERE id = ?", (site_id,))
    row2 = cur2.fetchone()
    conn2.close()

    if not row2:
        return False, '站点不存在'

    site_user, domain = row2
    local_dir = f'/opt/tpanel/backups/{site_user}'
    os.makedirs(local_dir, exist_ok=True)

    # rsync 从远程拉回
    ssh_cmd = f'ssh -o StrictHostKeyChecking=no -p {remote_port or 22}'
    if key_path and os.path.exists(key_path):
        ssh_cmd += f' -i {key_path}'

    local_tar = os.path.join(local_dir, os.path.basename(remote_file))

    rsync_cmd = [
        'rsync', '-avz',
        '-e', ssh_cmd,
        f'{remote_user}@{remote_host}:{remote_path}/{os.path.basename(remote_file)}',
        local_dir + '/'
    ]

    code, out, err = _run(rsync_cmd, timeout=600)

    if code != 0:
        return False, f'拉取失败: {err}'

    # 解压恢复
    if os.path.exists(local_tar):
        import tarfile
        try:
            site_path = f'/opt/tpanel/sites/{site_user}/'
            with tarfile.open(local_tar, 'r:gz') as tar:
                tar.extractall('/opt/tpanel/backups/')
            os.remove(local_tar)
        except Exception as e:
            return False, f'解压失败: {str(e)}'

    from system import write_log
    write_log('restore', f'远程恢复 {domain} from {remote_host}', '')

    return True, '恢复成功'

def get_backup_stats():
    """获取备份统计信息"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""SELECT type, COUNT(*) as cnt, SUM(size) as total_size
                          FROM backups GROUP BY type""")
    rows = cur.fetchall()
    conn.close()

    total_local = 0
    total_remote = 0
    count = 0

    for r in rows:
        if r[0] == 'local':
            total_local = r[2] or 0
            count += r[1]
        elif r[0] == 'remote':
            total_remote = r[2] or 0

    # 计算备份目录总大小
    code, out, _ = _run("du -sm /opt/tpanel/backups 2>/dev/null | awk '{print $1}'", shell=True)
    try:
        disk_used = int(out.strip()) if out.strip().isdigit() else 0
    except:
        disk_used = total_local / (1024 * 1024)

    return {
        'total_backups': count,
        'local_size_mb': round(total_local / (1024 * 1024), 1) if total_local else 0,
        'remote_size_mb': round(total_remote / (1024 * 1024), 1) if total_remote else 0,
        'disk_used_mb': disk_used,
    }