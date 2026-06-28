<?php
/**
 * TPanel phpMyAdmin 自动登录桥接 (v1.3.34+)
 *
 * phpMyAdmin Signon 模式要求 SignonURL 是一个 PHP 脚本：
 *   1. 接收 ?token=<HMAC> & db=<id>
 *   2. 验证 token（用 TPanel SECRET_KEY 同样的 HMAC 算法）
 *   3. 从共享 JSON 文件拿 db_user / db_pass（TPanel 后端写，PHP 读）
 *   4. session_start() + 设置 PMA_single_signon_* + 302 回 phpMyAdmin
 *
 * 安全：
 *   - SECRET_FILE 由 TPanel 后端 0600 tpanel:tpanel 拥有
 *   - 本脚本以 www-data 运行，需 sudo-less 读 tpanel.data 文件
 *   - 改用：把 secrets 写到 /etc/phpmyadmin/conf.d/tpanel-bridge.json 让 PHP 读
 */

declare(strict_types=1);

// 不显示 warning（生产友好）
error_reporting(E_ERROR | E_PARSE);

// 1. 读参数
$token = $_GET['token'] ?? '';
$db_id = $_GET['db'] ?? '';
if ($token === '' || $db_id === '' || !ctype_digit((string)$db_id)) {
    http_response_code(400);
    echo 'Missing token or db';
    exit;
}

// 2. 验证 token - 用 HMAC-SHA256，secret 从 bridge.json 读
$bridge_cfg = '/etc/phpmyadmin/conf.d/tpanel-bridge.json';
if (!file_exists($bridge_cfg)) {
    http_response_code(500);
    echo 'Bridge not configured';
    exit;
}
$cfg = json_decode(file_get_contents($bridge_cfg), true);
if (!is_array($cfg) || !isset($cfg['secret_key'])) {
    http_response_code(500);
    echo 'Bridge misconfigured';
    exit;
}
$secret = $cfg['secret_key'];

// Token 格式: <payload_b64>.<sig_b64>  (base64 + base64 padding 都保留)
$parts = explode('.', $token);
if (count($parts) !== 2) {
    http_response_code(400);
    echo 'Bad token';
    exit;
}
[$payload_b64, $sig_b64] = $parts;

$expected = hash_hmac('sha256', $payload_b64, $secret);
$expected_b64 = rtrim(strtr(base64_encode(hex2bin($expected)), '+/', '-_'), '=');
// 补回 base64 padding（v1.3.34 修复：Python 签时带 padding，PHP 验时不 rstrip）
$pad = strlen($expected_b64) % 4;
if ($pad) { $expected_b64 .= str_repeat('=', 4 - $pad); }
if (!hash_equals($expected_b64, $sig_b64)) {
    http_response_code(403);
    echo 'Invalid token signature';
    exit;
}

// 解码 payload（payload 自己也可能带 padding）
$payload_b64_padded = $payload_b64 . str_repeat('=', (-strlen($payload_b64)) % 4);
$payload_json = base64_decode(strtr($payload_b64_padded, '-_', '+/'), true);
if ($payload_json === false) {
    http_response_code(400);
    echo 'Bad payload';
    exit;
}
$payload = json_decode($payload_json, true);
if (!is_array($payload) || !isset($payload['db_id'], $payload['exp'])) {
    http_response_code(400);
    echo 'Bad payload fields';
    exit;
}
if ((int)$payload['db_id'] !== (int)$db_id) {
    http_response_code(403);
    echo 'DB id mismatch';
    exit;
}
if ((int)$payload['exp'] < time()) {
    http_response_code(403);
    echo 'Token expired';
    exit;
}

// 3. 拿 db_user / db_pass - 从 bridge.json 里读（TPanel 后端更新它）
if (!isset($cfg['dbs'][$db_id])) {
    http_response_code(404);
    echo 'DB not in bridge';
    exit;
}
$db = $cfg['dbs'][$db_id];

// 4. 启动 PHP session，配置 session 名（与 conf.d/tpanel-signon.php 一致）
session_name('TPanelSignon');
session_start();

$_SESSION['PMA_single_signon_user']     = $db['user'];
$_SESSION['PMA_single_signon_password'] = $db['pass'];
$_SESSION['PMA_single_signon_host']     = '127.0.0.1';
$_SESSION['PMA_single_signon_port']     = '';
$_SESSION['PMA_single_signon_socket']   = '';
$_SESSION['PMA_single_signon_auth_type'] = 'config';

// 关 session + 302 回 phpMyAdmin
$db_name = $db['name'];
session_write_close();
header('Location: https://zhangpu.tech/pma/index.php?db=' . urlencode($db_name));
exit;