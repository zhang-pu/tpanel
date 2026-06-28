#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.3.41 新增：SSL 证书同步工具（扫 /etc/letsencrypt/live/ 重建 ssl_certs）"""
import os
import sqlite3
import subprocess
from datetime import datetime

DB_PATH = "/opt/tpanel/data/tpanel.db"
SSL_DIR = "/etc/letsencrypt/live"


def sync_ssl_certs():
    """
    场景：apply_letsencrypt 申请证书成功但忘了写数据库 / 升级后数据库丢失
    返回: (added, updated, skipped, errors_list)
    """
    if not os.path.isdir(SSL_DIR):
        return (0, 0, 0, ["SSL 目录不存在: " + SSL_DIR])

    cert_dirs = [d for d in os.listdir(SSL_DIR)
                 if os.path.isdir(os.path.join(SSL_DIR, d)) and d != "README"]

    added, updated, skipped, errors = 0, 0, 0, []
    conn = sqlite3.connect(DB_PATH)

    for domain in cert_dirs:
        cert_path = SSL_DIR + "/" + domain + "/fullchain.pem"
        key_path = SSL_DIR + "/" + domain + "/privkey.pem"
        if not (os.path.exists(cert_path) and os.path.exists(key_path)):
            skipped += 1
            continue

        expire_date = None
        try:
            out = subprocess.run(
                ["openssl", "x509", "-in", cert_path, "-noout", "-enddate"],
                capture_output=True, text=True, timeout=5
            )
            for line in out.stdout.splitlines():
                if "notAfter=" in line:
                    raw = line.split("=", 1)[1].strip()
                    dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
                    expire_date = dt.strftime("%Y-%m-%d")
                    break
        except Exception as e:
            errors.append(domain + ": 解析证书失败 " + str(e))
            continue

        cur = conn.execute("SELECT id FROM sites WHERE domain=?", (domain,))
        row = cur.fetchone()
        site_id = row[0] if row else None

        cur = conn.execute("SELECT id FROM ssl_certs WHERE domain=?", (domain,))
        existing = cur.fetchone()
        if existing:
            conn.execute(
                "UPDATE ssl_certs SET cert_path=?, key_path=?, expire_date=?, site_id=COALESCE(?, site_id) WHERE id=?",
                (cert_path, key_path, expire_date, site_id, existing[0])
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO ssl_certs (site_id, domain, cert_path, key_path, expire_date, auto_renew) VALUES (?, ?, ?, ?, ?, 1)",
                (site_id, domain, cert_path, key_path, expire_date)
            )
            added += 1

    conn.commit()
    conn.close()
    return (added, updated, skipped, errors)
