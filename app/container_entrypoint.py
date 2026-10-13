"""Prepare mounted data, then run the application as the unprivileged user."""
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
from contextlib import closing


def prepare_data(folder, uid, gid):
    folder = Path(folder)
    if folder.is_symlink():
        raise RuntimeError('数据目录不能是符号链接')
    folder.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(folder, followlinks=False):
        for path in [Path(root)] + [Path(root) / name for name in files]:
            if path.is_symlink():
                continue
            info = path.stat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                continue
            os.chown(path, uid, gid)
            required = stat.S_IRUSR | stat.S_IWUSR
            if path.is_dir():
                required |= stat.S_IXUSR
            path.chmod(stat.S_IMODE(info.st_mode) | required)


def initialize():
    from .db import DB_PATH, init_db
    candidate = DB_PATH if DB_PATH.exists() else DB_PATH.parent / 'mahjong.db'
    has_users = False
    if candidate.exists():
        with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            has_users = 'users' in tables and bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())
    generated = None
    if not has_users and not os.environ.get('ADMIN_PASSWORD'):
        generated = secrets.token_urlsafe(18)
        os.environ['ADMIN_PASSWORD'] = generated
    init_db()
    if generated:
        print('首次初始化管理员账号：' + os.environ.get('ADMIN_USERNAME', 'admin'), flush=True)
        print('首次初始化管理员密码：' + generated, flush=True)
        print('请登录后在账号设置中修改密码。此密码只在首次初始化时输出。', flush=True)


def main():
    import pwd
    account = pwd.getpwnam('mahjong')
    if os.geteuid() == 0:
        prepare_data('/app/data', account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    command = sys.argv[1:]
    if not command:
        raise RuntimeError('缺少启动命令')
    if command[0] == 'uvicorn':
        initialize()
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
