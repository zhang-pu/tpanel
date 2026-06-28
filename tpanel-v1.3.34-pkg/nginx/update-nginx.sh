#!/bin/bash
# v1.3.34: 升级 nginx 配置加 /pma/ 路径(用于 phpMyAdmin 自动登录)
# 同时移除 8443 独立 server block(避免 SSL 错误)
# 用法: sudo bash update-nginx.sh
set -e

echo "[1/4] 备份当前配置..."
sudo cp /etc/nginx/sites-enabled/tpanel /etc/nginx/sites-enabled/tpanel.bak-v1334-$(date +%s)

echo "[2/4] 替换 /etc/nginx/sites-enabled/tpanel..."
sudo cp tpanel-https-with-pma.conf /etc/nginx/sites-enabled/tpanel

# 也删掉旧的 8443 server(避免 SSL 问题)
if [ -f /etc/nginx/sites-enabled/phpmyadmin.conf ]; then
    sudo rm /etc/nginx/sites-enabled/phpmyadmin.conf
    echo "  移除了旧的 /etc/nginx/sites-enabled/phpmyadmin.conf"
fi

echo "[3/4] 部署 phpMyAdmin 桥接脚本..."
if [ ! -f php-bridge/tpanel-bridge.php ]; then
    echo "ERROR: php-bridge/tpanel-bridge.php 不存在"
    exit 1
fi
sudo cp php-bridge/tpanel-bridge.php /usr/share/phpmyadmin/
sudo chown www-data:www-data /usr/share/phpmyadmin/tpanel-bridge.php
sudo cp php-bridge/tpanel-signon.php /etc/phpmyadmin/conf.d/
php -l /etc/phpmyadmin/conf.d/tpanel-signon.php

echo "[4/4] nginx -t + reload..."
sudo nginx -t && sudo systemctl reload nginx
echo ""
echo "✅ v1.3.34 /pma/ 路径已生效!"
echo "测试: curl -sI https://你的域名/pma/  应该返回 302 或 200"