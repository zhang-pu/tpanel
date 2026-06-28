# T面板 - 规格说明书

## 1. 项目概述

- **名称**：T面板（tpanel）
- **官方网址**：https://tpanel.cn
- **定位**：轻量级 Linux 网站管理面板，聚焦建站核心功能
- **目标用户**：个人站长、中小型网站管理者

## 2. 核心功能

### 2.1 网站管理
- 创建站点（绑定域名、选择PHP版本、设置目录）
- 删除站点（含数据确认）
- 站点列表（域名、状态、创建时间、备份状态）
- 站点起停（nginx reload/restart）
- 流量统计（nginx access log 解析）

### 2.2 SSL 证书
- Let's Encrypt 免费证书申请（HTTP验证）
- 证书自动续期（systemd timer 触发 certbot）
- 一键部署到站点
- 证书状态监控（剩余天数）

### 2.3 数据库管理
- 创建 MySQL 数据库 + 用户
- 删除数据库
- phpMyAdmin 一键安装（可选）
- 数据库列表（名称、大小、字符集）

### 2.4 备份系统
- 本地备份（tar + mysql dump）
- 远程备份（rsync 到另一台服务器）
- 定时备份（cron 表达式）
- 下载备份文件
- 恢复备份

### 2.5 安全功能
- 系统更新（apt-get security update）
- 站点数据隔离（Linux 用户分离）
- SSH 密钥管理（可选）
- 防火墙规则（UFW）
- 异常登录告警
- 自动漏洞修复（cron 定期执行）

### 2.6 文件管理
- 在线文件浏览
- 上传文件（压缩包自动解压）
- 编辑配置文件（高亮）
- 权限管理

## 3. 技术架构

### 3.1 目录结构
```
/opt/tpanel/
├── backend/
│   ├── main.py              # Flask 入口
│   ├── system.py            # 系统操作（nginx/mysql/backup）
│   ├── site.py              # 站点管理
│   ├── database.py          # 数据库管理
│   ├── ssl.py               # SSL 管理
│   ├── firewall.py          # 防火墙
│   ├── security.py          # 安全更新
│   ├── config.py            # 配置读写
│   └── utils.py             # 工具函数
├── frontend/
│   ├── index.html           # 主面板
│   ├── css/
│   ├── js/
│   └── assets/
├── data/                    # SQLite 数据库
├── sites/                   # 站点目录 /www/tpanel/sites/
├── backups/                 # 备份目录
├── logs/                    # 日志
├── ssl/                     # SSL 证书目录
└── config/                  # Nginx 配置 /etc/nginx/tpanel/

/etc/systemd/system/tpanel.service
/etc/nginx/tpanel-api.conf   # Nginx 反向代理
```

### 3.2 数据库表

```sql
-- 管理员账号
CREATE TABLE admin (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,  -- bcrypt
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME
);

-- 站点
CREATE TABLE sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    site_user TEXT NOT NULL UNIQUE,   -- Linux 用户名
    site_path TEXT NOT NULL,
    php_version TEXT DEFAULT '8.1',
    status TEXT DEFAULT 'running',     -- running/stopped
    ssl_enabled INTEGER DEFAULT 0,
    ssl_cert_path TEXT,
    ssl_key_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 数据库
CREATE TABLE databases (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id),
    name TEXT NOT NULL UNIQUE,
    db_user TEXT NOT NULL UNIQUE,
    db_pass TEXT NOT NULL,
    charset TEXT DEFAULT 'utf8mb4',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 备份记录
CREATE TABLE backups (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id),
    type TEXT DEFAULT 'local',       -- local/remote
    file_path TEXT,
    size INTEGER,
    status TEXT DEFAULT 'success',   -- success/failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- SSL 证书
CREATE TABLE ssl_certs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id),
    domain TEXT NOT NULL,
    cert_path TEXT NOT NULL,
    key_path TEXT NOT NULL,
    expire_date DATETIME,
    auto_renew INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 定时任务
CREATE TABLE cron_jobs (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id),
    name TEXT NOT NULL,
    schedule TEXT NOT NULL,          -- cron 表达式
    command TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_run DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 安全日志
CREATE TABLE security_logs (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    details TEXT,
    ip TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 设置
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

### 3.3 安全机制

1. **站点隔离**：每个站点一个 Linux 用户，Home 目录即网站根目录，禁 shell
2. **最小权限原则**：MySQL 用户权限精确到站点数据库
3. **CSRF 防护**：所有 POST 请求带 token 验证
4. **命令白名单**：仅允许预设的 shell 命令，无 shell 注入
5. **HTTPS**：面板强制 HTTPS，API 只能用 Token 认证
6. **文件上传限制**：仅允许 .zip/.tar.gz 上传，自动解压到站点目录
7. **日志审计**：所有操作写 security_logs
8. **自动修复**：每日 3:00 执行 apt-get update && apt-get upgrade -y

### 3.4 防火墙规则（UFW）

- 默认只开放 22（SSH）、80（HTTP）、443（HTTPS）
- 管理面板端口（例如 8848）仅限 localhost 访问
- Nginx 反向代理到面板后端

## 4. 面板设计

### 4.1 界面风格

- 风格：深色主题 + 绿色点缀（科技感、安全感）
- 字体：JetBrains Mono（代码）+ Inter（界面）
- 配色：主色 #22c55e（绿色），背景 #0f172a（深蓝黑），卡片 #1e293b

### 4.2 布局

```
┌─────────────────────────────────────────────────┐
│  [Logo] T面板          [站点数] [状态] [安全更新] │
├─────────────────────────────────────────────────┤
│                                                 │
│  [仪表盘] [网站] [数据库] [SSL] [备份] [安全]    │
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │              内容区域                     │   │
│  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## 5. API 设计

### 5.1 认证

```
POST /api/auth/login    { username, password }
POST /api/auth/logout
GET  /api/auth/check    (Header: Authorization: Bearer <token>)
```

### 5.2 站点

```
GET    /api/sites              列表
POST   /api/sites              创建 { domain, php_version, path }
GET    /api/sites/:id          详情
PUT    /api/sites/:id          更新 { status, ssl }
DELETE /api/sites/:id          删除
POST   /api/sites/:id/start     启动
POST   /api/sites/:id/stop      停止
POST   /api/sites/:id/backup    触发备份
```

### 5.3 数据库

```
GET    /api/databases
POST   /api/databases          创建 { site_id, name, user, pass }
DELETE /api/databases/:id
```

### 5.4 SSL

```
POST /api/ssl/apply             { site_id, domain }
GET  /api/ssl/certs             列表
POST /api/ssl/renew/:id         续期
```

### 5.5 备份

```
GET  /api/backups               列表
POST /api/backups               创建 { site_id, type }
GET  /api/backups/:id/download
POST /api/backups/:id/restore
DELETE /api/backups/:id
```

### 5.6 安全

```
GET  /api/security/logs          安全日志
POST /api/security/update       执行系统更新
GET  /api/security/status       安全状态（已安装更新数、漏洞数）
```

## 6. 安装流程

### 6.1 一键安装脚本

```bash
wget -O install.sh https://tpanel.cn/install.sh
bash install.sh
```

安装脚本做的事情：
1. 检测系统（Debian 10+ / Ubuntu 20.04+）
2. 安装 Nginx、MySQL、Python3、certbot
3. 创建 tpanel 用户和目录
4. 初始化 SQLite 数据库
5. 配置 systemd 服务
6. 配置 Nginx 反向代理
7. 申请 Let's Encrypt 面板证书
8. 启动服务

### 6.2 默认端口

- 面板访问：https://localhost:8848 （仅本地访问）
- 通过 Nginx 反代到域名，例如 https://tpanel.cn

## 7. 版本规划

### v1.0.0（首发）
- 站点 CRUD
- MySQL 数据库 CRUD
- Let's Encrypt SSL
- 本地备份
- 系统安全更新

### v1.1.0
- 远程备份（rsync）
- 定时任务
- 文件管理

### v1.2.0
- Cron 表达式验证
- 远程 rsync 备份 + 恢复
- 连接测试工具
- 备份统计面板

### v1.3.14（2026-06-07）
- ✅ 修复 ssl_manager.py 裸调 nginx -t / nginx -s reload（v1.3.11 漏改）
- ✅ 新增 tpanel-static-check.py 静态分析工具（10 项检查，不装机能抱 80% 装完才暴露的 bug）
- ✅ 提供 run-static-check.sh 入口脚本，发布前必跑

### v1.3.13（2026-06-07）
- ✅ 修复 sudo NOPASSWD 没生效（requiretty 阻挡 / 命令路径不一致）问题
- ✅ install.sh 用 `command -v` 动态探测真实路径，加 `!requiretty` 声明，加 `visudo -c` 验证，加 NOPASSWD 试跑
- ✅ 提供 `tpanel-fix-sudo.sh` 紧急补丁脚本
- ✅ 提供 `tpanel-install-test.sh` 装完自检脚本（6 节检查覆盖 v1.3.10~v1.3.12 全部隐藏问题）

### v1.3.12（2026-06-07）
- ✅ 修复软件安装 / 安全更新 SSE 流 “连接断开” 问题
- ✅ Nginx 为 `/api/tasks/<id>/stream` 拉专用 location：`proxy_read_timeout 1800s` + `proxy_buffering off` + `X-Accel-Buffering: no`
- ✅ 提供 `tpanel-fix-sse.sh` 紧急补丁脚本，老用户一键修复（只 reload nginx，不动 tpanel 服务）

### v1.3.11（2026-06-07）
- ✅ 修复全新装机后新建站点 `useradd: Permission denied` 的 bug（sudoers 漏授权）
- ✅ install.sh 新增 `/etc/sudoers.d/tpanel-admin`：useradd/userdel/usermod/chown/chmod/nginx/systemctl NOPASSWD
- ✅ system.py 全面加 `sudo` 前缀（useradd/userdel/set_site_permissions 的 chown/chmod/nginx -t + reload/nginx stop + start/apt-get update）

### v1.3.10（2026-06-07）
- ✅ 修复 phpMyAdmin 装完无 Nginx 8443 反代导致点 🐘 死循环 confirm 的 bug
- ✅ `task_manager.create_task` 新增 `on_complete(task_id, status)` 钩子（success/failed 都调，通用联动机制）
- ✅ 新增 `task_manager.setup_phpmyadmin_nginx()`：自动探 PMA 路径 → `sudo mv` 写 `/etc/nginx/sites-enabled/phpmyadmin.conf` → `nginx -t` → `systemctl reload nginx`（含安全加固）
- ✅ `api_phpmyadmin_status` 改三维判断（`sw_installed AND files_exist AND nginx_ok`），返回详细字段
- ✅ 前端 `openPhpMyAdmin` 改轮询 `_pollPhpMyAdminReady(30s)`，等 nginx_ok=true 再跳

### v1.3.9（2026-06-06，super release）
- ✅ 修复登录后必须强制刷新才能看到后台的 bug
- ✅ 仪表盘显示 CPU 核心数 + 型号
- ✅ 负载颜色按核心数判断

### v1.3.x（2026-06-05）
- ✅ PHP 5.6 / 7.0 / 7.4 / 8.0 / 8.1 / 8.2 / 8.3 软件市场一键装
- ✅ phpMyAdmin UI 一键装（之前无反代配置，v1.3.10 修）
- ✅ 软件市场后台任务流（SSE 实时进度）

### v1.3.0（待开发）
- Node.js 支持
- 日志查看器（nginx access/error log）

## 8. 开源协议

MIT License

## 9. 作者

- 作者：Zhang Pu
- 网站：https://zhangpu.dev
- 面板官网：https://tpanel.cn