#!/bin/bash
# T面板 - 一键安装脚本
# 官网: https://tpanel.cn
# 作者: Zhang Pu
set -e

echo "========================================"
echo "  🌿 T面板 v1.0.0 安装程序"
echo "  官网: https://tpanel.cn"
echo "  作者: Zhang Pu"
echo "========================================"
echo ""

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用 root 权限运行此脚本：sudo bash install.sh"
    exit 1
fi

# 检测系统
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VER=$VERSION_ID
    echo "检测到系统: $PRETTY_NAME"
else
    echo "❌ 无法识别系统版本"
    exit 1
fi

if [[ "$OS" == "ubuntu" ]] || [[ "$OS" == "debian" ]]; then
    PKG_MANAGER="apt-get"
elif [[ "$OS" == "centos" ]] || [[ "$OS" == "rocky" ]] || [[ "$OS" == "alma" ]]; then
    PKG_MANAGER="yum"
else
    echo "⚠️  未测试的系统 ($OS)，继续但可能出错"
fi

echo ""
echo "==> 1/7 更新软件源并升级系统..."
$PKG_MANAGER update -qq && $PKG_MANAGER upgrade -y

echo "==> 2/7 安装依赖包..."
if command -v nginx &>/dev/null; then
    echo "  Nginx 已安装，跳过"
else
    $PKG_MANAGER install -y nginx
fi

if command -v php &>/dev/null; then
    echo "  PHP 已安装，跳过"
else
    $PKG_MANAGER install -y php php-fpm php-mysql php-mbstring php-xml php-curl php-zip
fi

if command -v mariadb &>/dev/null; then
    echo "  MySQL 已安装，跳过"
else
    $PKG_MANAGER install -y mariadb-server
    systemctl enable mariadb
    systemctl start mariadb
fi

# Python3 和 pip
if ! command -v python3 &>/dev/null; then
    $PKG_MANAGER install -y python3 python3-pip python3-venv
fi

# certbot
if ! command -v certbot &>/dev/null; then
    $PKG_MANAGER install -y certbot python3-certbot-nginx
fi

echo "==> 3/7 创建 T面板 用户和目录..."
useradd -m -s /bin/bash tpanel 2>/dev/null || true
mkdir -p /opt/tpanel
mkdir -p /opt/tpanel/sites
mkdir -p /opt/tpanel/backups
mkdir -p /opt/tpanel/ssl
mkdir -p /opt/tpanel/logs
mkdir -p /opt/tpanel/data
mkdir -p /opt/tpanel/config

# 复制源码
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/backend/main.py" ]; then
    cp -r "$SCRIPT_DIR/backend" /opt/tpanel/
    cp -r "$SCRIPT_DIR/frontend" /opt/tpanel/
    cp "$SCRIPT_DIR/requirements.txt" /opt/tpanel/
    cp "$SCRIPT_DIR/SPEC.md" /opt/tpanel/
    echo "  源码已复制到 /opt/tpanel"
fi

chown -R tpanel:tpanel /opt/tpanel

echo "==> 4/7 安装 Python 依赖..."
cd /opt/tpanel
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
deactivate

echo "==> 5/7 初始化数据库..."
cd /opt/tpanel/backend
chown -R tpanel:tpanel /opt/tpanel
sudo -u tpanel bash -c "source /opt/tpanel/venv/bin/activate && python3 db_init.py"

echo "==> 6/7 配置 Nginx 反向代理..."
cat > /etc/nginx/sites-available/tpanel.conf << 'EOF'
# TPanel - https://tpanel.cn
server {
    listen 80;
    server_name localhost;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8848;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

ln -sf /etc/nginx/sites-available/tpanel.conf /etc/nginx/sites-enabled/tpanel.conf

# 删除默认配置
rm -f /etc/nginx/sites-enabled/default

nginx -t && nginx -s reload

echo "==> 7/7 配置 Systemd 服务..."
cat > /etc/systemd/system/tpanel.service << 'EOF'
[Unit]
Description=TPanel - Linux Website Management Panel
Documentation=https://tpanel.cn
After=network.target mariadb.service

[Service]
Type=simple
User=tpanel
Group=tpanel
WorkingDirectory=/opt/tpanel/backend
Environment="PATH=/opt/tpanel/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/opt/tpanel/venv/bin/python3 main.py 8848
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tpanel
systemctl start tpanel

echo ""
echo "✅ T面板安装完成！"
echo ""
echo "   访问地址：http://localhost （或服务器 IP）"
echo "   默认账号：admin / tpanel.cn"
echo "   后台端口：8848"
echo ""
echo "   官方网址：https://tpanel.cn"
echo "   作者：Zhang Pu · https://zhangpu.dev"
echo ""
echo "   常用命令："
echo "   systemctl status tpanel   # 查看状态"
echo "   systemctl restart tpanel  # 重启面板"
echo "   journalctl -u tpanel -f    # 查看日志"
echo ""