"""
TPanel - 文件管理模块
"""
import os
import zipfile
import tarfile
import shutil
import subprocess
from datetime import datetime

def _run(cmd, timeout=30):
    try:
        if isinstance(cmd, str):
            cmd = cmd.split()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out'
    except Exception as e:
        return -1, '', str(e)

def list_directory(path, site_user=None):
    """列出目录内容，带安全和权限信息"""
    # 安全检查：防止路径遍历
    real_path = os.path.realpath(path)
    allowed_base = ['/opt/tpanel/sites', '/opt/tpanel/backups']
    if not any(real_path.startswith(base) for base in allowed_base):
        return None, '路径不在允许范围内'

    if not os.path.exists(path):
        return None, '目录不存在'

    items = []
    try:
        entries = os.listdir(path)
    except PermissionError:
        return None, '无权限访问'

    for name in sorted(entries):
        fp = os.path.join(path, name)
        try:
            stat = os.stat(fp)
            is_dir = os.path.isdir(fp)

            # 文件大小
            if is_dir:
                size = sum(os.path.getsize(os.path.join(dp, f))
                           for dp, dn, fn in os.walk(fp) for f in fn) if False else 0
            else:
                size = stat.st_size

            items.append({
                'name': name,
                'type': 'dir' if is_dir else 'file',
                'size': size,
                'size_str': format_size(size),
                'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                'permissions': stat.st_mode & 0o777,
                'perm_str': format_permissions(stat.st_mode & 0o777),
                'readable': os.access(fp, os.R_OK),
                'writable': os.access(fp, os.W_OK),
            })
        except Exception:
            continue

    return items, None

def format_size(size):
    if size < 1024:
        return str(size) + ' B'
    elif size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    elif size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    else:
        return f'{size / (1024 * 1024 * 1024):.2f} GB'

def format_permissions(mode):
    chars = ['---', '--x', '-w-', '-wx', 'r--', 'r-x', 'rw-', 'rwx']
    return chars[(mode >> 6) & 7] + chars[(mode >> 3) & 7] + chars[mode & 7]

def read_file(path, max_size=1024 * 1024):
    """读取文件内容（限制1MB）"""
    if not os.path.exists(path):
        return None, '文件不存在'
    if os.path.getsize(path) > max_size:
        return None, '文件超过 1MB 限制'

    # 只允许读取配置文件和常见文本格式
    allowed_ext = ['.php', '.html', '.htm', '.css', '.js', '.json', '.txt', '.md',
                   '.yaml', '.yml', '.xml', '.conf', '.ini', '.log', '.sql']
    ext = os.path.splitext(path)[1].lower()
    if ext not in allowed_ext and not any(path.endswith(x) for x in ['/config.php', '/.htaccess']):
        return None, '文件类型不允许读取'

    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)

def write_file(path, content):
    """写入文件（仅限站点目录）"""
    real_path = os.path.realpath(path)
    if not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, '文件已保存'
    except Exception as e:
        return False, str(e)

def upload_file(upload_dir, file_obj, filename):
    """上传文件到站点目录"""
    real_path = os.path.realpath(upload_dir)
    if not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    # 限制文件类型
    allowed = ['.php', '.html', '.htm', '.css', '.js', '.json', '.txt', '.md',
               '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico',
               '.zip', '.tar', '.gz', '.bz2',
               '.pdf', '.doc', '.docx', '.xls', '.xlsx',
               '.woff', '.woff2', '.ttf', '.eot']
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed:
        return False, f'文件类型 {ext} 不允许上传'

    dest = os.path.join(upload_dir, filename)
    try:
        file_obj.save(dest)
        # 自动解压 zip/tar.gz
        if filename.endswith('.zip'):
            try:
                with zipfile.ZipFile(dest, 'r') as zf:
                    zf.extractall(upload_dir)
                return True, f'文件已上传并解压：{filename}'
            except Exception:
                return True, f'文件已上传（解压失败）：{filename}'
        elif filename.endswith(('.tar.gz', '.tgz')):
            try:
                with tarfile.open(dest, 'r:gz') as tf:
                    tf.extractall(upload_dir)
                return True, f'文件已上传并解压：{filename}'
            except Exception:
                return True, f'文件已上传（解压失败）：{filename}'

        return True, f'文件已上传：{filename}'
    except Exception as e:
        return False, str(e)

def delete_file(path):
    """删除文件或目录"""
    real_path = os.path.realpath(path)
    if not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, '已删除'
    except Exception as e:
        return False, str(e)

def chmod_file(path, mode):
    """修改文件权限（限制范围）"""
    real_path = os.path.realpath(path)
    if not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    # 限制权限范围（v1.3.20+：接受 755/644 等十进制字符串）
    try:
        if isinstance(mode, str):
            mode = int(mode, 8)  # '755' -> 0o755 = 493
        elif isinstance(mode, int) and mode < 0o1000:
            # 看起来是 755 这种小数（不是 0o755），自动当八进制解释
            mode = int(str(mode), 8) if mode < 1000 else mode
    except (ValueError, TypeError):
        return False, '权限值格式错误（应该是 755、644 这种）'
    if mode & 0o777 not in [0o755, 0o644, 0o600, 0o700, 0o775, 0o664]:
        return False, f'权限值不允许（{oct(mode & 0o777)}，可选 755/644/600/700/775/664）'

    try:
        os.chmod(path, mode & 0o777)
        return True, f'权限已修改为 {oct(mode & 0o777)}'
    except Exception as e:
        return False, str(e)

def create_directory(path, dirname):
    """创建目录"""
    real_path = os.path.realpath(path)
    if not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    new_path = os.path.join(path, dirname)
    try:
        os.makedirs(new_path, exist_ok=True)
        return True, f'目录已创建：{dirname}'
    except Exception as e:
        return False, str(e)