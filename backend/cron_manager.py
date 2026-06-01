"""
TPanel - 定时任务管理模块
"""
import os
import sqlite3
import subprocess
from datetime import datetime
from config import DB_PATH

def _run(cmd, timeout=30, shell=False):
    try:
        if isinstance(cmd, str) and not shell:
            cmd = cmd.split()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=shell)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out'
    except Exception as e:
        return -1, '', str(e)

def get_all_crons():
    """获取所有定时任务"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""SELECT c.*, s.domain FROM cron_jobs c
                          LEFT JOIN sites s ON c.site_id = s.id
                          ORDER BY c.id DESC""")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows

def create_cron(site_id, name, schedule, command):
    """
    创建定时任务
    schedule: cron 表达式，如 "0 3 * * *" (每天3点)
    command: 要执行的命令
    """
    # 验证 cron 表达式格式
    parts = schedule.strip().split()
    if len(parts) != 5:
        return None, 'Cron 表达式格式错误，需要 5 段：分 时 日 月 周'

    # 生成一个唯一文件名
    import hashlib
    token = hashlib.md5(f'{site_id}{name}{command}{datetime.now()}'.encode()).hexdigest()[:12]
    script_name = f'cron_{token}.sh'

    # 写入站点目录的 cron 脚本
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_user FROM sites WHERE id = ?", (site_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None, '站点不存在'

    site_user = row[0]
    cron_dir = f'/opt/tpanel/sites/{site_user}/.cron'
    os.makedirs(cron_dir, exist_ok=True)

    script_path = os.path.join(cron_dir, script_name)
    with open(script_path, 'w') as f:
        f.write(f'#!/bin/bash\n{command}\n')
    os.chmod(script_path, 0o755)

    # 写入系统 crontab（用 sudo 切换到站点用户执行）
    cron_line = f'{schedule} sudo -u {site_user} {script_path} >> /opt/tpanel/logs/cron_{token}.log 2>&1'

    # 读取现有 crontab
    code, out, err = _run(f'crontab -l 2>/dev/null || echo ""', shell=True)
    existing = out if code == 0 else ''

    # 检查是否已有同名任务
    lines = [l for l in existing.split('\n') if script_name not in l and l.strip()]
    lines.append(cron_line)

    # 写回 crontab
    new_cron = '\n'.join(lines) + '\n'
    code, out, err = _run(f'echo "{new_cron}" | crontab -', shell=True, timeout=10)

    if code != 0:
        os.remove(script_path)
        return None, f'Crontab 写入失败: {err}'

    # 写入数据库
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""INSERT INTO cron_jobs (site_id, name, schedule, command)
                          VALUES (?, ?, ?, ?)""",
                       (site_id, name, schedule, command))
    conn.commit()
    cron_id = cur.lastrowid
    conn.close()

    return cron_id, '定时任务创建成功'

def delete_cron(cron_id):
    """删除定时任务"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT name, command FROM cron_jobs WHERE id = ?", (cron_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, '任务不存在'

    name, command = row

    # 从 crontab 移除
    code, out, err = _run('crontab -l 2>/dev/null || echo ""', shell=True)
    if code == 0 and out:
        lines = [l for l in out.split('\n') if name not in l and l.strip()]
        _run(f'echo "{chr(10).join(lines)}\n" | crontab -', shell=True, timeout=10)

    # 删除脚本文件
    cron_dir = '/opt/tpanel/sites'
    for site_dir in os.listdir('/opt/tpanel/sites'):
        script = os.path.join(cron_dir, site_dir, '.cron')
        if os.path.exists(script):
            for f in os.listdir(script):
                if name in f:
                    try:
                        os.remove(os.path.join(script, f))
                    except:
                        pass

    conn.execute("DELETE FROM cron_jobs WHERE id = ?", (cron_id,))
    conn.commit()
    conn.close()

    return True, '任务已删除'

def enable_cron(cron_id, enabled):
    """启用/禁用定时任务"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE cron_jobs SET enabled = ? WHERE id = ?", (1 if enabled else 0, cron_id))

    # 如果禁用，从 crontab 注释掉；如果启用，恢复
    cur = conn.execute("SELECT name, schedule, command FROM cron_jobs WHERE id = ?", (cron_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, '任务不存在'

    name, schedule, command = row
    prefix = '' if enabled else '#'

    # 简单处理：重新生成 crontab
    # 获取所有启用的任务重新写入
    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.execute("SELECT name, schedule, command, enabled FROM cron_jobs WHERE enabled = 1")
    enabled_rows = cur2.fetchall()
    conn2.close()

    lines = []
    for r in enabled_rows:
        n, s, c = r[0], r[1], r[2]
        import hashlib
        token = hashlib.md5(f'{n}{c}'.encode()).hexdigest()[:12]
        lines.append(f'{s} sudo -u {get_site_user_by_name(n)} /opt/tpanel/sites/{get_site_user_by_name(n)}/.cron/cron_{token}.sh >> /opt/tpanel/logs/cron_{token}.log 2>&1')

    if enabled:
        _run(f'echo "{"".join([l + chr(10) for l in lines])}" | crontab -', shell=True, timeout=10)

    return True, f'任务已{"启用" if enabled else "禁用"}'

def get_site_user_by_name(name):
    """根据任务名查找站点用户（辅助）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_user FROM sites LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 'tpanel'

def run_cron_now(cron_id):
    """立即执行定时任务（手动触发）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT site_id, name, command FROM cron_jobs WHERE id = ?", (cron_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, '任务不存在'

    site_id, name, command = row

    conn2 = sqlite3.connect(DB_PATH)
    cur2 = conn2.execute("SELECT site_user FROM sites WHERE id = ?", (site_id,))
    row2 = cur2.fetchone()
    conn2.close()

    if not row2:
        return False, '站点不存在'

    site_user = row2[0]

    # 以站点用户身份执行命令
    code, out, err = _run(
        f'sudo -u {site_user} bash -c "{command}"',
        shell=True, timeout=60
    )

    # 更新最后执行时间
    conn3 = sqlite3.connect(DB_PATH)
    conn3.execute("UPDATE cron_jobs SET last_run = ? WHERE id = ?",
                  (datetime.now().isoformat(), cron_id))
    conn3.commit()
    conn3.close()

    return code == 0, out if code == 0 else err

def validate_cron_expression(expr):
    """验证 cron 表达式是否有效"""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False, '需要 5 段：分 时 日 月 周'

    labels = ['分', '时', '日', '月', '周']
    ranges = [
        (0, 59),   # 分: 0-59
        (0, 23),   # 时: 0-23
        (1, 31),   # 日: 1-31
        (1, 12),   # 月: 1-12
        (0, 6),    # 周: 0-6 (0=周日)
    ]

    for i, (part, (lo, hi)) in enumerate(zip(parts, ranges)):
        if part == '*':
            continue
        if '/' in part:
            base, step = part.split('/')
            if not step.isdigit():
                return False, f'{labels[i]} 步长必须是数字'
            continue
        if ',' in part:
            for p in part.split(','):
                try:
                    v = int(p)
                    if v < lo or v > hi:
                        return False, f'{labels[i]} 范围 {lo}-{hi}'
                except:
                    return False, f'{labels[i]} 包含无效值'
            continue
        if '-' in part:
            start, end = part.split('-')
            try:
                if int(start) < lo or int(end) > hi:
                    return False, f'{labels[i]} 范围 {lo}-{hi}'
            except:
                return False, f'{labels[i]} 格式错误'
            continue
        try:
            v = int(part)
            if v < lo or v > hi:
                return False, f'{labels[i]} 范围 {lo}-{hi}'
        except:
            return False, f'{labels[i]} 包含无效字符'

    return True, '格式正确'