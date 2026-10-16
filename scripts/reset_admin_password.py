#!/usr/bin/env python3
"""重置 admin 后台账号密码（用于已有部署从旧弱密码升级到新默认密码）。

背景：默认种子密码已从 `admin123` 改为 `Grape@510630`（见 db_setup._seed_admin_accounts）。
该默认值只在 `admin_accounts` 为空（首次部署）时生效；已初始化过的库不会自动改密，
本脚本用于对存量部署执行一次性改密。

行为：
    - 定位 admin 账号（默认 username=admin），更新 password_hash；
    - token_version+1 → 该账号所有旧 admin JWT 立即失效；
    - 置 meta `admin_pwd_initial`：新密码等于当前默认值时为 1（前端仍提示首登改密），否则为 0。

环境：DEVOPS_DB_PATH 指向目标库（默认 data/sessions.db）；MySQL 后端按 mysql_compat 配置。

用法：
    python scripts/reset_admin_password.py                       # dry-run：仅显示将重置哪个账号
    python scripts/reset_admin_password.py --apply               # 重置为默认密码 Grape@510630
    python scripts/reset_admin_password.py --apply -p 'Xx@123456'  # 重置为指定密码
    python scripts/reset_admin_password.py --apply --create      # 账号不存在时一并创建
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import src.admin.db as admin_db  # noqa: E402
from src.user.db import set_meta_value  # noqa: E402

DEFAULT_PASSWORD = "Grape@510630"
MIN_PASSWORD_LEN = 8


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset admin account password")
    parser.add_argument("-u", "--username", default="admin", help="admin 用户名（默认 admin）")
    parser.add_argument("-p", "--password", default=DEFAULT_PASSWORD,
                        help=f"新密码（默认 {DEFAULT_PASSWORD}）")
    parser.add_argument("--apply", action="store_true", help="执行写库；默认 dry-run")
    parser.add_argument("--create", action="store_true", help="账号不存在时创建")
    args = parser.parse_args()

    if len(args.password) < MIN_PASSWORD_LEN:
        print(f"[reset-admin] 密码长度至少 {MIN_PASSWORD_LEN} 位", file=sys.stderr)
        return 2

    admin = admin_db.get_admin_by_username(args.username)
    if not admin:
        if not args.create:
            print(f"[reset-admin] 账号不存在: {args.username}"
                  f"（如需创建请加 --create）", file=sys.stderr)
            return 1
        if not args.apply:
            print(f"[reset-admin] dry-run：将创建账号 {args.username} 并设置新密码")
            return 0
        admin_id = admin_db.create_admin_account(args.username, args.password)
        admin_db.increment_admin_token_version(admin_id)
        _mark_initial(args.password)
        print(f"[reset-admin] 已创建账号 {args.username}（id={admin_id}）并设置新密码")
        return 0

    if not args.apply:
        print(f"[reset-admin] dry-run：将重置账号 {args.username}（id={admin['id']}）的密码"
              f"（add --apply to write）")
        return 0

    admin_db.update_admin_password(admin["id"], args.password)
    new_ver = admin_db.increment_admin_token_version(admin["id"])
    _mark_initial(args.password)
    print(f"[reset-admin] 已重置账号 {args.username}（id={admin['id']}）密码，"
          f"token_version={new_ver}，旧会话已失效")
    return 0


def _mark_initial(password: str):
    """仍为默认密码 → 置 1（前端引导改密）；否则置 0。"""
    set_meta_value("admin_pwd_initial", "1" if password == DEFAULT_PASSWORD else "0")


if __name__ == "__main__":
    sys.exit(main())
