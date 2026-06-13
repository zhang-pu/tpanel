# TPanel 变更日志（CHANGELOG）

> **作者**: Zhang Pu  
> **官网**: https://tpanel.cn  
> **GitHub**: https://github.com/zhang-pu/tpanel  
> **协议**: MIT  
> **发布周期**: 紧急修复为主，无固定周期  
> **版本约定**: v1.3.X 中，X 是累计迭代号；只有经过实机验证的稳定版会发 GitHub Release  
> **本日志涵盖**: v1.3.0 (2026-05-30) → v1.3.40 (2026-06-12)

---

## 📦 v1.3.40 — 2026-06-12 【正式发布】

> **重点**: phpMyAdmin 自动登入 + 装包自愈 + dpkg 二次校验

### 🐛 关键修复

1. **phpMyAdmin 装包失败后 files_exist 误判**
   - dpkg 标记 `Status: install ok installed` 但 `/usr/share/phpmyadmin` 实际 missing（partial install）
   - 前端 status 报"已装"但实际 502 / 找不到目录
   - 修：加 `dpkg -V phpmyadmin` 二次校验 + 自动 `apt-get install --reinstall`

2. **setup_phpmyadmin_nginx 缺 self-heal**
   - 找不到 pma 目录就 silently return False，没给运维任何提示
   - 修：开头加 `dpkg -V` 探测 + `apt-get install --reinstall` 自动重装
   - 修：加 `ss -tln | grep :9000` 探测 PHP-FPM listen，不通就 warn 日志（不再 silent 502）

3. **`/api/phpmyadmin/token/<id>` 端点缺失**（v1.3.34 前端依赖）
   - 前端点数据库名时调 `/api/phpmyadmin/token/<id>` 拿 5 分钟 HMAC token
   - 后端 main.py 一直没实现这个端点 → 404 → 前端 `window.open` 不执行 → 用户点"没反应"
   - 修：新增 `api_phpmyadmin_token` + `api_phpmyadmin_signon` 端点

4. **api_phpmyadmin_signon 写 bridge.json Permission denied**
   - `/usr/share/phpmyadmin` 是 root 755，tpanel 用户没写权限
   - 修：用 `sudo mv /tmp/bridge.json` + `sudo chmod 644` 两步

5. **CONFIG 缺 SECRET_KEY**
   - HMAC token 签发需要密钥，config.py 没有
   - 修：启动时 `secrets.token_hex(32)` 随机生成 64 字符 hex

### ✨ 新增功能

1. **phpMyAdmin 自动登入**（点数据库名 → 直接进 pma）
   - HMAC token 5 分钟有效，绑 db_id
   - signon 端点写 `bridge.json`（db_user/db_pass/ts）
   - nginx 8443 反代 + 8400 PHP-FPM listen
   - `tpanel-bridge.php` 桥接 session → pma 自动用 db_user 登入
   - `/etc/phpmyadmin/config.inc.php` 启用 `config` auth，读 signon data 自动填账号

### 📝 教训

- 永远别让 dpkg 静默失败——必须用 `dpkg -V` 二次校验
- v1.3.34 前端加了 token 端点，但后端 main.py 一直没合并过来——半年才被发现
- 写装包脚本必须在干净机器上真跑一遍（这次踩坑 6/10 装包 partial，6/12 才暴露）

---

## 📦 v1.3.39 — 2026-06-11 【开发版，未发 release】

### 🐛 修复

1. **软件卸载改用 `purge` 而非 `remove`**
   - `remove` 保留配置（`/etc/nginx/sites-enabled/*.conf` 不删）
   - 改 `purge` 完全删干净
2. **加 on_complete 钩子到卸载流程**
   - 卸载 PHP 后自动 reload nginx + 改 8848 配置
3. **task_manager dpkg -s 误判修复**
   - `dpkg -s` 对 `deinstall ok config-files` 状态返 0，会误判"已装"
   - 加 explicit check 区分 installed / config-files

---

## 📦 v1.3.38 — 2026-06-11 【开发版，未发 release】

### 🐛 修复

1. **fastcgi_pass 端口按 PHP 版本动态选**
   - 之前硬编码 `127.0.0.1:9000`，多 PHP 版本时 8.1/8.2/8.3 混用
   - 修：ssl_manager.py 写 nginx conf 时按 db 的 php_version 字段选对应端口
2. **nginx reload 在 file change 事件触发**
   - 修：inotify 等价实现（轮询 mtime）

---

## 📦 v1.3.37 — 2026-06-10 【开发版，未发 release】

### 🐛 修复

1. **on_complete 钩子无 Flask request context**
   - 后台线程跑钩子时 `request.remote_addr` 不可用 → 报 AttributeError
   - 修：钩子里 `try/except`，None 当 fallback
2. **write_log 接受 None ip**
   - 部分场景 ip 是 None 时报 TypeError

---

## 📦 v1.3.36 — 2026-06-09 【开发版，未发 release】

### ✨ 新增

1. **get_installed_php_versions() 函数**
   - system.py 新增，扫描 `/usr/bin/php*` + dpkg -l
   - 前端创建站点时只列已装 PHP（避免选错导致 502）

2. **建站时 PHP 版本校验**
   - 前端二次校验（即使后端也校验了）
   - 未装的 PHP 选项 disabled
   - 防止用户选 PHP 5.6 装了一半发现 FPM 没起来

---

## 📦 v1.3.35 — 2026-06-08 【正式发布】

> **重点**: 文件管理权限调整

### 🐛 修复

1. **lscpu 在容器/Docker 无 "Model name" 行导致 Unknown CPU**
   - 仪表盘显示 `Unknown CPU`
   - 修：`/proc/cpuinfo` fallback 读 model name
2. **chmod UI 修复**（前端）
   - 数字校验、3 位 0-7 限制、rwx 实时预览
   - 暴露 `parsePermMode` 处理 0o755 八进制 vs '755' 字符串两种输入

### 发布

- GitHub Release: `v1.3.35` 文件管理权限调
- 源码包: `/work/tpanel-v1.3.35-source.zip`

---

## 📦 v1.3.34 — 2026-06-07 【开发版，未发 release】

### ✨ 新增

1. **phpMyAdmin Signon 模式前端支持**
   - 点数据库名 / 🐘 按钮调 `_openPmaWithAutoLogin(dbId, dbName)`
   - 拿 5 分钟 HMAC token → 跳 `/api/phpmyadmin/signon?token=...&db=1`
   - 跳 `/tpanel-bridge.php?token=...&db=1` → 302 到 pma
   - **后端 main.py 当时没实现 token + signon 端点**（直到 v1.3.40 才补）

2. **改数据库密码前端 UI**
   - 🔑 按钮 → 输入新密码 → 调 `/api/databases/<id>/password`
   - 后端 v1.3.34 同时实现 change_db_password

---

## 📦 v1.3.33 — 2026-06-08 【正式发布】

### 杂项

- 一些 UI 调整
- cron manager 优化

### 发布

- GitHub Release: `v1.3.33`

---

## 📦 v1.3.30 — 2026-06-08 【正式发布】

> **重点**: 一键安装脚本 v1.3.30（合并 v1.3.22 ~ v1.3.29 累积修复）

### 🐛 合并自 1.3.22 ~ 1.3.29

- v1.3.22 移动端侧边栏 ☰ 按钮
- v1.3.23 phpMyAdmin URL 不再硬编码 127.0.0.1
- v1.3.24 SSL .well-known 查 sqlite 拿真 site_path
- v1.3.25 SSL 申请走任务流 + SSE 进度框
- v1.3.26 站点类型 php|static + 静态 index.html
- v1.3.27 SSE query string 传 token（EventSource 不能自定义 header）
- v1.3.28 add Sury PHP 源
- v1.3.29 on_complete 钩子自动配 PHP-FPM listen + 批量重写 nginx conf 按 PHP 版本

### 发布

- GitHub Release: `v1.3.30`
- 官网: https://tpanel.cn/install.sh 同步更新

---

## 📦 v1.3.14 — 2026-06-07 【正式发布】

> **重点**: `static-check.py` 静态分析工具

### 🐛 问题

v1.3.10 ~ v1.3.13 四轮迭代反复「装机后才发现」—— sandbox 里没 docker/systemd，不能跑完整 install.sh。但**所有 4 轮 bug 都是同一个模式**：「双向闭环」一边配一边调用漏一边。

### ✨ 解法

写个 `static-check.py` 静态分析工具，从源码挖双向闭环 bug：

1. 高危命令 `_run` 裸调（查 `_run(['cmd', ...]` 中 cmd 是否在 DANGEROUS_CMDS 且无 sudo）
2. install.sh sudoers 双向闭环
3. 前端 `/api/` 路由 vs main.py `@app.route` 一致性
4. import 模块存在性
5. 版本号一致性
6. Nginx 端口 vs main.py 监听端口
7. on_complete 钩子函数定义存在
8. phpMyAdmin 反代路径探测逻辑完整
9. Nginx SSE location 含 1800s timeout
10. sudoers 含 `!requiretty`

### 🐛 首跑挖出 v1.3.14 候选 bug

`ssl_manager.py` 108/112/291/295 行裸调 nginx（v1.3.11 漏改）—— 已修复。

### 发布

- `/work/tpanel-v1.3.14-source.zip` (79KB)
- `/work/tpanel-static-check.py` (16KB, md5=a87524aebffff4e81015ef4249bf3c9c)
- `/work/tpanel-v1.3.14-docs.zip` (16KB)

### 📝 教训

写 install.sh 必须在干净 VPS 上真跑一遍，5 分钟的事。考虑加 install-test 自动化测试。

---

## 📦 v1.3.13 — 2026-06-07 【正式发布】

### 🐛 修复

1. **sudo NOPASSWD 实际不生效 → `sudo: a terminal is required to read the password`**
   - v1.3.11 硬编码 `/usr/sbin/useradd` 在某些 Debian minimal 镜像上不对
   - 被 `Defaults requiretty` 全局设置挡住 NOPASSWD
   - 修：install.sh 用 `command -v` 动态探测命令路径
   - 修：sudoers 加 `Defaults:tpanel !requiretty` 关键声明
   - 修：`visudo -c -f` 语法验证
   - 修：装完立刻试跑 NOPASSWD
   - 提供 `tpanel-fix-sudo.sh` 紧急补丁脚本

2. **提供 `tpanel-install-test.sh` 装完自检脚本**（6 节检查）
   - 系统基本 / sudoers NOPASSWD / Nginx SSE / phpmyadmin 反代 / 端到端 API / 服务健康

---

## 📦 v1.3.12 — 2026-06-07 【正式发布】

### 🐛 修复

1. **SSE 连接断开**（用户装机实测）
   - Nginx 默认 `proxy_read_timeout 60s`，apt install/upgrade 静默 30s+ 是常态
   - 60s 到点 Nginx 主动断开代理，浏览器 EventSource 看到"连接断开"
   - 修：install.sh Nginx 配置为 `/api/tasks/<id>/stream` 加专用 location
     - `proxy_read_timeout 1800s` + `proxy_buffering off` + `X-Accel-Buffering: no` + `proxy_cache off`
   - 提供 `tpanel-fix-sse.sh` 紧急补丁

---

## 📦 v1.3.11 — 2026-06-07 【正式发布】

### 🐛 修复

1. **全新装机新建站点报 `useradd: Permission denied`**
   - v1.3.10 install.sh 只为 mysql 授权 sudoers
   - system.py 中 useradd/userdel/chown/chmod/nginx -s reload 全是裸调
   - 新建站点第一步创建系统用户就 100% 失败
   - 修：install.sh 新增 `/etc/sudoers.d/tpanel-admin`
     - NOPASSWD 授权 useradd/userdel/usermod/chown/chmod/nginx/systemctl
     - `/usr/sbin/` + `/usr/bin/` 都列上（Debian/CentOS 路径不同）
   - 修：system.py 全面加 sudo 前缀

### 📝 教训

凡是要 root 权限的命令（useradd/chown/nginx 等），system.py 写了 sudo 必须配 sudoers；反过来，install.sh 加 sudoers 必须 system.py 真的调了 sudo——两边要对得上

---

## 📦 v1.3.10 — 2026-06-07 【正式发布】

> **重点**: phpMyAdmin on_complete 钩子 + 软件市场 + 任务管理

### 🐛 修复

1. **phpMyAdmin 装完无反代 → 点 🐘 死循环 confirm**
   - v1.3.10 装完 phpMyAdmin 后没有自动写 Nginx 8443 反代配置
   - `/api/phpmyadmin/status` 永远返回 `nginx_ok=false`
   - 前端 `setTimeout(..., 1000)` 跳走再调 status 又触发 confirm
   - 修：`setup_phpmyadmin_nginx` 函数实现 + on_complete 钩子

2. **软件市场 + 任务管理**
   - software 表 + tasks 表
   - `create_task(name, cmd, on_complete=...)` 通用接口
   - 实时进度通过 SSE 推前端
   - 装完自动调 on_complete 钩子

3. **预检环境**
   - `create_site` 前检查：磁盘空间 / 内存 / nginx 状态

### 发布

- `/work/tpanel-v1.3.10-source.zip` (61KB, md5=1b6b3ef02ce05ce6a609899d71b3ed0d)
- tpanel.cn/install.sh 同步更新

---

## 📦 v1.3.9 — 2026-06-06 【正式发布】

> **重点**: super release，合并 6/6 全部修复

### 🐛 关键修复

1. **登录后必须强制刷新才能看到后台**（v1.3.8 时代就有，**用户实测发现**）
   - 根因：`showLogin()` 用 `document.body.innerHTML = '...'` 整个重写 body
   - 把后台骨架（aside.sidebar + main.main）全部销毁
   - 结果：login() 成功后 `initApp() → setupNav()` 找不到 `.nav-item[data-page]`
   - `loadDashboard()` 静默失败（getElementById 返 null 被 try/catch 吞掉）
   - 现象：登录后页面卡在登录页，必须 Ctrl+Shift+R 才能进后台
   - **修法**：登录页改独立 `<div id="loginScreen">`，后台骨架包一层 `<div id="appShell">`
   - `showLogin/hideLogin` 改 display 切换
   - `logout()` 也改用 showLogin() 而非 location.reload()

### 📝 教训

- **永远别 innerHTML 重写 body**；前端 SPA 登录前后页面元素应该一直在 DOM 里
- **发布前必跑一次完整登录流程**，API 200 不等于 UI 正常

### 发布

- `/work/tpanel-v1.3.9-source.zip` (49KB, md5=69b461098fc2429533dec918f976f0ad)

---

## 📦 v1.3.8 — 2026-06-05 【正式发布】

> **重点**: 8 个隐藏 bug 一次性修

1. **zip 魔数校验**：用 `grep -q "PK\x03\x04"`（grep 文本模式不解析 \x），所有合法 zip 都被误判为 404 HTML
   - 改用 `od -An -tx1 -N4` + hex 字符串比较
2. **解压漏复制**：只 `cp backend/ frontend/`，没复制根目录的 `requirements.txt`、`.gitignore` 等文件
3. **`/etc/nginx/tpanel` 权限**：config.py import 时就 `os.makedirs('/etc/nginx/tpanel')`，tpanel 用户无权限
   - install.sh 补上 `mkdir -p && chown tpanel:tpanel`
4. **systemd ExecStart 没传端口**：ExecStart 写的是 `python main.py`（没传参）
5. **前端 JS 2 处语法错误**（v1.0.0 时代就有，从未暴露）
6. **PHP-FPM 完全没装**（最大坑！所有建 PHP 站的人全 404）
   - install.sh 加 `php8.2-fpm + 扩展` 自动装
7. **PHP-FPM unix socket 在 systemd 环境失效**
   - 改用 TCP `127.0.0.1:9000`
8. **MySQL/MariaDB 完全没装 + shell=True SQL 注入**（最危险！）
   - install.sh 加 mariadb-server + sudoers
   - system.py create_mysql_db/delete_mysql_db 改用 subprocess list + sudo + regex 校验

### 📝 教训

写 install.sh 必须在干净 VPS 上真跑一遍，5 分钟的事。考虑加 install-test 自动化测试。

---

## 📦 v1.3.4 — 2026-06-05 【开发版，未发 release】

3 个 install.sh 隐藏 bug 修复（同 v1.3.8 的 1/2/3 项）

---

## 📦 v1.3.32 — 2026-06-08 【开发版，未发 release】

### 🐛 修复

- sidebar `!important` 保险（避免主题变量被覆盖）
- 跟 v1.3.31 配套：v1.3.31 改白色背景 + v1.3.32 加 `!important` 保险

---

## 📦 v1.3.31 — 2026-06-08 【开发版，未发 release】

### 🐛 修复

- 侧边栏改白色背景（CSS 变量方案）
- 主题适配
- 配套 v1.3.32 加 `!important` 保险

---

## 📦 v1.3.21 — 2026-06-07 【正式发布】

> **重点**: cron/run 走任务流 + sudo 修整收尾

### 🐛 修复

1. **cron/run 走任务流**（不再阻塞 HTTP）
2. **run_security_update 第二步 sudo**
   - `apt-get update` 后需要 sudo 跑第二步
3. **ssl_manager 写 conf 失败修复**
4. **certbot 加 sudo**
   - 之前用裸调 certbot 报权限错

### 交付

- 源码包 83KB（md5=0677c9095b493f6d35acfe6d14fa4022）
- 文档包 19KB

---

## 📦 v1.3.20 — 2026-06-07 【正式发布】

### 🐛 修复

- `chmod_file` 不接受 `'755'` 字符串
- 统一 octal 转换（前端 0o755 字符串双兼容）

---

## 📦 v1.3.19 — 2026-06-07 【正式发布】

### 🐛 修复

- `/api/settings` PUT 双重存储 bug
  - 写 conf 文件，但 `get_panel_domain` 读 sqlite → 配置不生效
  - 修：统一读 sqlite（conf 改成只读 fallback）

---

## 📦 v1.3.18 — 2026-06-07 【正式发布】

### ✨ 新增

- 新增 `/api/auth/change-password` 端点
- 前端"修改密码"功能可用

---

## 📦 v1.3.17 — 2026-06-07 【正式发布】

### 🐛 修复

- `backup.lastrowid` 拿不到刚插入的 id
  - sqlite3 `INSERT ... RETURNING` 不支持
  - 修：先 `cur.lastrowid`
- `restore` PermissionError
- `time` import 缺失

---

## 📦 v1.3.16 — 2026-06-07 【正式发布】

### 🐛 修复

- `fastcgi_pass unix:127.0.0.1:9000` 拼接错语法
  - 模板 f-string 拼接时把两个地址拼一起
  - 修：模板改成 `'127.0.0.1:9000'` 直接字符串

---

## 📦 v1.3.15 — 2026-06-07 【正式发布】

### 🐛 修复

- `write_nginx_config` 写 `/etc/nginx` 失败
  - 路径权限问题，system.py 走 sudo
- `DEBIAN_FRONTEND` sudo 拒绝
  - certbot 需要 `DEBIAN_FRONTEND=noninteractive`
  - sudo 启动时把环境变量吃掉
  - 修：`sudo -E` 保留环境
- `sudo bash -c` 嵌套问题

---

## 📦 v1.3.4 — 2026-06-05 【开发版，未发 release】

3 个 install.sh 隐藏 bug（v1.3.8 段详细描述的 1/2/3 项提前修）：

1. **zip 魔数校验**：用 `grep -q "PK\x03\x04"`（grep 文本模式不解析 \x）
   - 改用 `od -An -tx1 -N4` + hex 字符串比较
2. **解压漏复制**：只 `cp backend/ frontend/`，没复制根目录的 `requirements.txt`、`.gitignore` 等
3. **`/etc/nginx/tpanel` 权限**：config.py import 时就 `os.makedirs('/etc/nginx/tpanel')`，tpanel 用户无权限
   - install.sh 补上 `mkdir -p && chown tpanel:tpanel`

---

## 📦 v1.3.3 — 2026-06-05 【开发版，未发 release】

小修补。

---

## 📦 v1.3.2 — 2026-06-05 【正式发布】

### 🐛 致命 bug

v1.3.1 的 install.sh 用 `grep -q "PK\x03\x04"` 校验 zip 魔数，但 grep 文本模式不解析 \x 转义，所以**永远拒绝所有 zip**，5 个下载源全挂。

### 修法

改用 `od -An -tx1 -N4` + hex 字符串比较（`504b0304`），端到端测试通过。

### 📝 教训

写校验代码必须真实验证，不能"看起来对"。

---

## 📦 v1.3.1 — 2026-06-04 【正式发布】

### ✨ install.sh 健壮性大幅提升（9.5KB）

- 4 个下载源自动回退：Release → latest → tag → main 分支
- zip 文件魔数校验（PK\x03\x04），自动识别 404 HTML 不再解压报错
- 本地兜底：自动扫描 `/tmp/tpanel*.zip`
- 修复：main.py 中 `get_panel_domain` 在空配置下的 500 错误

### 发布

- GitHub Release: `v1.3.1`
- tpanel.cn/install.sh 同步更新
- 源码包: `/work/tpanel-v1.3.1-source.zip` (47KB)

---

## 📦 v1.3.0 — 2026-05-30 【稳定版】

首次正式发版。Python Flask + SQLite + 原生 HTML/CSS/JS 单文件前端。

### 已实现

- 网站管理（增删改查 + nginx conf 自动写）
- 数据库管理（MariaDB）
- SSL 证书（Let's Encrypt 申请 / 续期）
- 备份（本地 + 远程 rsync）
- 文件管理（上传 / 编辑 / 权限）
- 定时任务（cron 增删 + 立即执行）
- 安全（每日 03:00 自动 apt upgrade + UFW 防火墙）
- 域名绑定（限制后台访问来源）

### 设计目标

- 单 VPS 80/443 端口默认站 + 多个 vhost
- 一键安装：`wget -O install.sh https://tpanel.cn/install.sh && bash install.sh`
- 默认账号 `admin / tpanel.cn`，服务路径 `/opt/tpanel`

---

## 📋 待办 / 路线图

## 📦 v1.3.41 — 2026-06-13 【正式发布】

> **重点**: Dashboard 数字色修复 + SSL 证书同步 + PHP 动态下拉

### 🐛 关键修复

1. **Dashboard 数字色修复**
   - v1.3.40 把 `.stat-card .value` 颜色写死 `#fff`，light 主题（粉卡片）下完全看不见
   - 改成 `var(--text)` 跟随主题（dark 主题显示浅色，light 主题显示深色）
   - 受影响：📁 网站总数 / 🗄️ 数据库 / 💾 备份状态 / 🔐 安全更新 4 个数字

2. **数据库页面图标错位**
   - `.data-table td` 加 `vertical-align: middle`
   - 30px 高的图标按钮跟 13px 文字 baseline 不对齐
   - 修复后 4 列完美居中

3. **SSL 证书同步按钮（新功能）**
   - 加 `/api/ssl/sync` API + 前端 `🔄 同步证书库` 按钮
   - 扫 `/etc/letsencrypt/live/` 重建 `ssl_certs` 表
   - 解决 v1.3.40 升级时 `apply_letsencrypt` 漏写数据库的 bug
   - 适用场景：数据库丢失 / 不一致 / 跨机迁移

4. **PHP 版本下拉框优化**
   - 加 `/api/system/php-versions` API
   - 前端动态拉已装版本：已装 ✓ / 未装灰显
   - 选中未装版本时红字提示「⚠️ 新建站点时会 502」+ 跳软件市场安装链接
   - 默认值改成 8.2（v1.3.40 默认 8.1 但很多镜像没装 8.1）

### 🆕 新文件

- `backend/ssl_sync.py`（SSL 证书同步工具）

### 📊 验证

- 端到端：API 返回 `{"added":0,"updated":3,"skipped":0,"errors":[]}` ✅
- 静态分析：py_compile 通过 + JS 括号配对 ✅
- 前端：3 处新代码（按钮 + 函数 + 调用）✅
- 后端：5 个新路由/函数（`api_ssl_sync`, `api_system_php_versions` 等）✅

---

### v1.3.41+ 候选

- **自动注入 pma Signon session 优化**：当前靠 `/tmp/tpanel_signon_data.json` + config auth 凑合
  - 下个版本尝试 pma 5.2 原生 `SignonSession` 模式（不用 config auth）
- **多 PHP 版本切换前端**（用户能选 7.4/8.0/8.1/8.2/8.3/8.4）
- **Node.js 支持**（类似 PHP 装包）
- **nginx 日志查看器**（v1.3+）
- **TPanel 密码修改功能**（前端已占位，后端缺实现）
- **打印机故障诊断**（图片识别问题待解决）

### 商业化（Freemium 模式）

- 免费版：全功能使用，页面必须保留作者链接（Powered by TBlog/TPanel）
- 专业版：付费去除链接，解锁高级功能（多站点管理、高级备份、技术支持）
- 技术方案：激活码许可证系统（类似 WordPress/JetBrains）

---

## 🔖 版本号约定

- 末位 +1 = 紧急修复（任何时机）
- 末位 +2 = 累积新功能（每月）
- 主版本不动（v1.3 → v2.0 是大重构）
- 注释里 `# v1.3.X` 必标（这个 changelog 才有依据）

---

**最后更新**: 2026-06-13 16:30 CST  
**编辑**: Zhang Pu via OpenClaw MiniMax-M3 (v1.3.41 push)
