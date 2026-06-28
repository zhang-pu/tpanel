<?php
// TPanel phpMyAdmin 自动登录配置（v1.3.34）
// 用 /pma/ 路径走主域名 SSL,不再用 8443 端口
$cfg["Servers"][1]["auth_type"] = "signon";
$cfg["Servers"][1]["SignonSession"] = "TPanelSignon";
$cfg["Servers"][1]["SignonURL"] = "https://zhangpu.tech/pma/tpanel-bridge.php";
$cfg["Servers"][1]["LogoutURL"] = "https://zhangpu.tech/dashboard";
