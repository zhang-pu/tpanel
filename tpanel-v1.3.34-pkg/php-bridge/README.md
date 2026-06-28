# TPanel phpMyAdmin 自动登录桥接（v1.3.34+）

## 文件说明

| 文件 | 安装到 | 用途 |
|------|--------|------|
| `tpanel-bridge.php` | `/usr/share/phpmyadmin/tpanel-bridge.php` | Signon 端点：验 token + 启动 PHP session + 302 回 phpMyAdmin |
| `tpanel-signon.php` | `/etc/phpmyadmin/conf.d/tpanel-signon.php` | phpMyAdmin 配置：auth_type=signon + SignonSession=TPanelSignon + SignonURL 指向 bridge |

## 部署时机

由 `task_manager.setup_phpmyadmin_nginx` 在 phpMyAdmin 安装完成后自动部署。

不需要用户手动操作。

## 工作流程

```
点 db_name
  ↓
前端 GET /api/phpmyadmin/token/<db_id>
  ↓
后端签 5 分钟有效 HMAC token
  ↓
window.open("/api/phpmyadmin/signon?token=xxx&db=1")
  ↓
后端 302 到 /tpanel-bridge.php?token=xxx&db=1
  ↓
PHP bridge 验 token + 查 bridge.json 拿 db_user/db_pass
  ↓
session_start() + 设置 $_SESSION[PMA_single_signon_*]
  ↓
302 到 phpMyAdmin (自动登录)
```

