"""
TPanel - 文件管理模块
v1.3.42 修复：支持管理员模式任意目录读写 + sudo提权
"""
import os
import zipfile
import tarfile
import shutil
import subprocess
from datetime import datetime

def _run(cmd, timeout=30, sudo=False):
    """执行命令，支持sudo提权"""
    try:
        if isinstance(cmd, str):
            cmd = cmd.split()
        if sudo:
            cmd = ['sudo', '-n'] + cmd
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out'
    except Exception as e:
        return -1, '', str(e)

def list_directory(path, site_user=None, admin_mode=False):
    """列出目录内容，带安全和权限信息
    admin_mode=True：允许浏览任意目录（管理员模式）
    """
    # 安全检查：防止路径遍历
    real_path = os.path.realpath(path)
    allowed_base = ['/opt/tpanel/sites', '/opt/tpanel/backups']
    if not admin_mode and not any(real_path.startswith(base) for base in allowed_base):
        return None, '路径不在允许范围内'
    
    # 管理员模式下禁止访问系统关键目录
    if admin_mode:
        blocked_paths = ['/proc', '/sys', '/dev', '/run', '/var/lib/mysql', '/root/.ssh']
        for blocked in blocked_paths:
            if real_path.startswith(blocked):
                return None, '系统关键目录不允许访问'

    if not os.path.exists(path):
        return None, '目录不存在'

    items = []
    try:
        entries = os.listdir(path)
    except PermissionError:
        # 管理员模式下无权限尝试sudo
        if admin_mode:
            code, stdout, stderr = _run(f'ls -1A {path}', sudo=True)
            if code == 0:
                entries = stdout.split('\n')
            else:
                return None, '无权限访问'
        else:
            return None, '无权限访问'

    for name in sorted(entries):
        fp = os.path.join(path, name)
        try:
            stat = os.stat(fp)
            is_dir = os.path.isdir(fp)

            # 文件大小
            if is_dir:
                size = 0
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
                'readable': os.access(fp, os.R_OK) or admin_mode,
                'writable': os.access(fp, os.W_OK) or admin_mode,
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

def read_file(path, max_size=1024 * 1024, admin_mode=False):
    """读取文件内容（限制1MB）
    admin_mode=True：允许读取任意文本文件
    """
    real_path = os.path.realpath(path)
    if not os.path.exists(real_path):
        return None, '文件不存在'
    
    # 检查文件大小
    try:
        file_size = os.path.getsize(real_path)
    except:
        if admin_mode:
            code, stdout, stderr = _run(f'stat -c %s {real_path}', sudo=True)
            if code == 0:
                file_size = int(stdout.strip())
            else:
                return None, '无法获取文件大小'
        else:
            return None, '无权限读取文件'
    
    if file_size > max_size:
        return None, f'文件超过 {max_size//1024}KB 限制'

    # 非管理员模式：只允许读取配置文件和常见文本格式
    if not admin_mode:
        allowed_ext = ['.php', '.html', '.htm', '.css', '.js', '.json', '.txt', '.md',
                       '.yaml', '.yml', '.xml', '.conf', '.ini', '.log', '.sql']
        ext = os.path.splitext(path)[1].lower()
        if ext not in allowed_ext and not any(path.endswith(x) for x in ['/config.php', '/.htaccess']):
            return None, '文件类型不允许读取'

    try:
        # 尝试普通读取
        with open(real_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read(), None
    except PermissionError:
        if admin_mode:
            # 管理员模式用sudo读取
            code, stdout, stderr = _run(f'cat {real_path}', sudo=True)
            if code == 0:
                return stdout, None
            else:
                return None, f'读取失败: {stderr}'
        else:
            return None, '无权限读取文件'
    except Exception as e:
        return None, str(e)

def write_file(path, content, admin_mode=False):
    """写入文件
    admin_mode=True：允许写入任意路径，自动sudo提权
    """
    real_path = os.path.realpath(path)
    if not admin_mode and not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    # 管理员模式下禁止写入系统关键文件
    if admin_mode:
        blocked_paths = ['/proc', '/sys', '/dev', '/run', '/var/lib/mysql', '/root/.ssh', '/etc/sudoers', '/etc/passwd', '/etc/shadow']
        for blocked in blocked_paths:
            if real_path.startswith(blocked):
                return False, '系统关键文件不允许修改'

    try:
        # 先尝试普通写入
        with open(real_path, 'w', encoding='utf-8') as f:
            f.write(content)
        # 确保站点目录权限正确（非管理员模式）
        if not admin_mode and real_path.startswith('/opt/tpanel/sites'):
            _run(f'chown tpanel:tpanel {real_path}', sudo=True)
        return True, '文件已保存'
    except PermissionError:
        if admin_mode or real_path.startswith('/opt/tpanel/sites'):
            # 用sudo tee写入
            proc = subprocess.run(
                ['sudo', '-n', 'tee', real_path],
                input=content.encode('utf-8'),
                capture_output=True,
                timeout=10
            )
            if proc.returncode == 0:
                # 确保文件权限正常
                _run(f'chmod 644 {real_path}', sudo=True)
                return True, '文件已保存'
            else:
                return False, f'写入失败: {proc.stderr.decode()}'
        else:
            return False, '无权限写入文件'
    except Exception as e:
        return False, str(e)

def upload_file(upload_dir, file_obj, filename, admin_mode=False):
    """上传文件到目录
    admin_mode=True：允许上传到任意路径
    """
    real_path = os.path.realpath(upload_dir)
    if not admin_mode and not real_path.startswith('/opt/tpanel/sites'):
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
        # 非管理员模式下修正权限
        if not admin_mode:
            _run(f'chown tpanel:tpanel {dest}', sudo=True)
        return True, f'文件已上传：{filename}'
    except PermissionError:
        if admin_mode or real_path.startswith('/opt/tpanel/sites'):
            # 先写到临时文件再sudo移动
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                file_obj.save(tmp.name)
                tmp_path = tmp.name
            code, stdout, stderr = _run(f'mv {tmp_path} {dest}', sudo=True)
            if code == 0:
                _run(f'chmod 644 {dest}', sudo=True)
                return True, f'文件已上传：{filename}'
            else:
                os.unlink(tmp_path)
                return False, f'上传失败: {stderr}'
        else:
            return False, '无权限上传文件'
    except Exception as e:
        return False, str(e)

def delete_file(path, admin_mode=False):
    """删除文件或目录
    admin_mode=True：允许删除任意路径，自动sudo提权
    """
    real_path = os.path.realpath(path)
    if not admin_mode and not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    # 管理员模式下禁止删除系统关键目录
    if admin_mode:
        blocked_paths = ['/proc', '/sys', '/dev', '/run', '/var/lib/mysql', '/root/.ssh', '/etc', '/usr', '/bin', '/sbin', '/opt/tpanel/venv', '/opt/tpanel/backend']
        for blocked in blocked_paths:
            if real_path.startswith(blocked) and real_path != blocked.rstrip('/'):
                return False, '系统关键目录不允许删除'

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, '已删除'
    except PermissionError:
        if admin_mode or real_path.startswith('/opt/tpanel/sites'):
            if os.path.isdir(path):
                code, stdout, stderr = _run(f'rm -rf {path}', sudo=True)
            else:
                code, stdout, stderr = _run(f'rm -f {path}', sudo=True)
            if code == 0:
                return True, '已删除'
            else:
                return False, f'删除失败: {stderr}'
        else:
            return False, '无权限删除'
    except Exception as e:
        return False, str(e)

def chmod_file(path, mode, admin_mode=False):
    """修改文件权限（限制范围）
    admin_mode=True：允许修改任意路径权限
    """
    real_path = os.path.realpath(path)
    if not admin_mode and not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    # 解析权限
    try:
        if isinstance(mode, str):
            mode = int(mode, 8)
        elif isinstance(mode, int) and mode < 0o1000:
            mode = int(str(mode), 8) if mode < 1000 else mode
    except (ValueError, TypeError):
        return False, '权限值格式错误（应该是 755、644 这种）'
    
    perm = mode & 0o777
    if not admin_mode and perm not in [0o755, 0o644, 0o600, 0o700, 0o775, 0o664]:
        return False, f'权限值不允许（{oct(perm)}，可选 755/644/600/700/775/664）'

    try:
        os.chmod(path, perm)
        return True, f'权限已修改为 {oct(perm)}'
    except PermissionError:
        if admin_mode or real_path.startswith('/opt/tpanel/sites'):
            code, stdout, stderr = _run(f'chmod {oct(perm)[2:]} {path}', sudo=True)
            if code == 0:
                return True, f'权限已修改为 {oct(perm)}'
            else:
                return False, f'修改权限失败: {stderr}'
        else:
            return False, '无权限修改权限'
    except Exception as e:
        return False, str(e)

def create_directory(path, dirname, admin_mode=False):
    """创建目录
    admin_mode=True：允许在任意路径创建目录
    """
    real_path = os.path.realpath(path)
    if not admin_mode and not real_path.startswith('/opt/tpanel/sites'):
        return False, '路径不在允许范围内'

    new_path = os.path.join(path, dirname)
    try:
        os.makedirs(new_path, exist_ok=True)
        if not admin_mode:
            _run(f'chown -R tpanel:tpanel {new_path}', sudo=True)
        return True, f'目录已创建：{dirname}'
    except PermissionError:
        if admin_mode or real_path.startswith('/opt/tpanel/sites'):
            code, stdout, stderr = _run(f'mkdir -p {new_path}', sudo=True)
            if code == 0:
                if not admin_mode:
                    _run(f'chown -R tpanel:tpanel {new_path}', sudo=True)
                return True, f'目录已创建：{dirname}'
            else:
                return False, f'创建目录失败: {stderr}'
        else:
            return False, '无权限创建目录'
    except Exception as e:
        return False, str(e)


def extract_archive(archive_path, target_dir, delete_after=False, admin_mode=False):
    """解压压缩包到目标目录
    支持 zip / tar / tar.gz / tgz
    v1.3.41: 带 zip slip / tar slip 防护
    """
    real_archive = os.path.realpath(archive_path)
    real_target = os.path.realpath(target_dir)
    # 安全:必须在允许的路径下
    if not admin_mode and not real_archive.startswith('/opt/tpanel/sites'):
        return False, '压缩包路径不在允许范围内'
    if not admin_mode and not real_target.startswith('/opt/tpanel/sites'):
        return False, '目标路径不在允许范围内'
    if not os.path.isfile(real_archive):
        return False, '压缩包不存在'

    filename = os.path.basename(real_archive).lower()
    file_count = 0
    try:
        if filename.endswith('.zip'):
            with zipfile.ZipFile(real_archive, 'r') as zf:
                # 防 zip slip: 拒绝 ../ 跳出 target
                for member in zf.namelist():
                    member_path = os.path.realpath(os.path.join(real_target, member))
                    if not member_path.startswith(real_target):
                        return False, f'压缩包含非法路径: {member}'
                zf.extractall(real_target)
                file_count = len(zf.namelist())
        elif filename.endswith('.tar.gz') or filename.endswith('.tgz'):
            with tarfile.open(real_archive, 'r:gz') as tf:
                for member in tf.getmembers():
                    member_path = os.path.realpath(os.path.join(real_target, member.name))
                    if not member_path.startswith(real_target):
                        return False, f'压缩包含非法路径: {member.name}'
                tf.extractall(real_target)
                file_count = len(tf.getmembers())
        elif filename.endswith('.tar'):
            with tarfile.open(real_archive, 'r') as tf:
                for member in tf.getmembers():
                    member_path = os.path.realpath(os.path.join(real_target, member.name))
                    if not member_path.startswith(real_target):
                        return False, f'压缩包含非法路径: {member.name}'
                tf.extractall(real_target)
                file_count = len(tf.getmembers())
        else:
            return False, '仅支持 .zip / .tar.gz / .tgz / .tar 格式'
        
        # 非管理员模式下修正权限
        if not admin_mode and real_target.startswith('/opt/tpanel/sites'):
            _run(f'chown -R tpanel:tpanel {real_target}', sudo=True)
            
    except zipfile.BadZipFile:
        return False, '不是有效的 zip 文件'
    except tarfile.ReadError:
        return False, '不是有效的 tar 文件'
    except PermissionError:
        return False, '无权限解压文件'
    except Exception as e:
        return False, f'解压失败: {str(e)}'

    if delete_after:
        try:
            os.remove(real_archive)
        except Exception as e:
            return True, f'已解压 {file_count} 个文件（删除压缩包失败: {e}）'

    return True, f'已解压 {file_count} 个文件到 {os.path.relpath(real_target, "/opt/tpanel/sites") if real_target.startswith("/opt/tpanel/sites") else real_target}'
