# TPanel - 轻量级 Linux 网站管理面板

🛡️ 安全高效的 Linux 网站管理面板，聚焦建站核心功能，开源免费。

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.3.14-blue.svg)](https://github.com/zhang-pu/tpanel/releases/tag/v1.3.14)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org)

## 特点

- 🌐 **站点管理** - 一键创建站点、绑定域名、切换 PHP 版本
- 🔐 **免费 SSL** - Let's Encrypt 证书一键申请、自动续期
- 🗄️ **数据库** - 在线创建 MySQL 数据库和用户，精确权限控制
- 💾 **备份恢复** - 本地备份、远程 rsync 备份，定时自动执行
- 🛡️ **安全防护** - 站点用户隔离、每日自动安全更新、防火墙规则
- 📁 **文件管理** - 在线浏览、上传、编辑，权限可视化修改
- 🐘 **phpMyAdmin** - UI 一键安装，8443 端口独立反代，IP 直访，账号复用站点 db_user/db_pass
- 🧩 **多 PHP 版本** - PHP 5.6 ~ 8.3 一键装，切站点时选版本
- ⚡ **软件市场** - 软件列表 + 后台任务流（SSE 实时进度），apt/yum 自动适配

## 系统要求

- Ubuntu 20.04+ / Debian 10+ / CentOS 7+
- 1GB+ 内存
- Nginx / PHP / MySQL（安装脚本自动安装）

## 安装

一行命令安装（自动适配 Ubuntu / Debian / CentOS）：

```bash
wget -O install.sh https://tpanel.cn/install.sh && bash install.sh
```

**v1.3.14** 包含：站点管理、数据库、SSL、备份、文件管理、定时任务、安全防护、CPU 核心数/型号显示、软件市场（PHP 多版本 + phpMyAdmin）、phpMyAdmin 装完自动配 Nginx 8443 反代、sudoers 完整授权（动态路径 + !requiretty + visudo 验证 + 试跑）、SSE 流不断开、装完自检脚本（6 节）、静态分析工具（10 项检查，发布前必跑）。

详见 [CHANGELOG.md](CHANGELOG.md)

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