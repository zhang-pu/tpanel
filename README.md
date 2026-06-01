# TPanel - 轻量级 Linux 网站管理面板

🛡️ 安全高效的 Linux 网站管理面板，聚焦建站核心功能，开源免费。

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 特点

- 🌐 **站点管理** - 一键创建站点、绑定域名、切换 PHP 版本
- 🔐 **免费 SSL** - Let's Encrypt 证书一键申请、自动续期
- 🗄️ **数据库** - 在线创建 MySQL 数据库和用户，精确权限控制
- 💾 **备份恢复** - 本地备份、远程 rsync 备份，定时自动执行
- 🛡️ **安全防护** - 站点用户隔离、每日自动安全更新、防火墙规则
- 📁 **文件管理** - 在线浏览、上传、编辑，权限可视化修改

## 系统要求

- Ubuntu 20.04+ / Debian 10+ / CentOS 7+
- 1GB+ 内存
- Nginx / PHP / MySQL（安装脚本自动安装）

## 安装

```bash
wget -O install.sh https://tpanel.cn/install.sh
bash install.sh
```

安装完成后访问 `https://your-server.com`，默认账号：`admin` / `tpanel.cn`

## 技术栈

- **后端**: Python3 + Flask + SQLite
- **前端**: 原生 HTML/CSS/JS（零依赖）
- **Web**: Nginx 反向代理
- **证书**: Let's Encrypt + certbot

## 目录结构

```
/opt/tpanel/
├── backend/       # Flask API
├── frontend/     # Web UI
├── data/         # SQLite 数据库
├── sites/        # 站点目录
├── backups/      # 备份文件
├── ssl/          # SSL 证书
└── logs/         # 日志
```

## 安全设计

- 站点用户隔离（每个站点一个 Linux 用户）
- 每日凌晨自动安全更新
- UFW 防火墙（默认只开放 80/443/22）
- CSRF Token 验证
- 操作日志审计

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。

## 作者

**Zhang Pu** - [zhangpu.dev](https://zhangpu.dev)

- 官网: https://tpanel.cn
- 文档: https://docs.tpanel.cn