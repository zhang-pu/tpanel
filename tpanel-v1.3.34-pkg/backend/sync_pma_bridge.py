#!/usr/bin/env python3
"""
TPanel → phpMyAdmin 自动登录桥接同步脚本（v1.3.34）
当数据库 db_pass 修改后调用，把 secret_key + 所有 db 凭证写到
/etc/phpmyadmin/conf.d/tpanel-bridge.json（PHP 端读）
"""
import json
import os
import sys
import sqlite3
import subprocess
import datetime

DB_PATH = '/opt/tpanel/data/tpanel.db'
BRIDGE_FILE = '/etc/phpmyadmin/conf.d/tpanel-bridge.json'
SECRET_FILE = '/opt/tpanel/data/.secret_key'


def sync_bridge():
    """同步所有数据库凭证到 bridge.json"""
    if not os.path.exists(SECRET_FILE):
        print('SECRET_KEY file missing', file=sys.stderr)
        sys.exit(1)
    with open(SECRET_FILE, 'r') as f:
        secret_key = f.read().strip()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT id, name, db_user, db_pass FROM databases")
    dbs = {}
    for row in cur.fetchall():
        dbs[str(row[0])] = {
            'name': row[1],
            'user': row[2],
            'pass': row[3],
        }
    conn.close()

    payload = {
        'secret_key': secret_key,
        'dbs': dbs,
        'updated_at': datetime.datetime.now().isoformat(),
    }
    # 先写到 /tmp(可写),再 sudo mv
    tmp = '/tmp/tpanel-bridge.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(payload, f)
    os.chmod(tmp, 0o644)

    r = subprocess.run(['sudo', 'mv', tmp, BRIDGE_FILE], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'mv failed: {r.stderr}', file=sys.stderr)
        sys.exit(1)
    r = subprocess.run(['sudo', 'chmod', '644', BRIDGE_FILE], capture_output=True)
    r = subprocess.run(['sudo', 'chown', 'www-data:www-data', BRIDGE_FILE], capture_output=True)
    print(f'synced {len(dbs)} dbs to {BRIDGE_FILE}')


if __name__ == '__main__':
    sync_bridge()