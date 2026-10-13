"""Offline maintenance. Run restore while the web service is stopped."""
import argparse
import getpass
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .db import DB_PATH, TZ, db, init_db, audit, now


def main():
    parser=argparse.ArgumentParser(description='雀会数据库维护')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    reset=sub.add_parser('reset-password');reset.add_argument('username')
    restore=sub.add_parser('restore');restore.add_argument('file');restore.add_argument('--service-stopped',action='store_true',required=True)
    export=sub.add_parser('snapshot');export.add_argument('output')
    args=parser.parse_args()
    if args.command=='restore':
        source_path=Path(args.file).resolve()
        if source_path==DB_PATH.resolve():raise SystemExit('不能用当前数据库作为恢复来源')
        with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('备份完整性检查失败')
            tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users','records'}.issubset(tables):raise SystemExit('不是可用的雀会数据库')
            if DB_PATH.exists():
                retained=DB_PATH.with_name('before-restore-'+datetime.now(TZ).strftime('%Y%m%d-%H%M%S')+'.db')
                with closing(sqlite3.connect(DB_PATH)) as previous,closing(sqlite3.connect(retained)) as saved:previous.backup(saved)
                print('恢复前快照：'+str(retained))
            DB_PATH.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(DB_PATH)) as target:source.backup(target)
        init_db()
        with db() as c:
            c.execute('DELETE FROM sessions')
            audit(c,None,'restore',source_path.name)
        print('恢复完成；请启动服务并重新登录')
    elif args.command=='snapshot':
        target_path=Path(args.output).resolve()
        if target_path==DB_PATH.resolve():raise SystemExit('快照不能覆盖当前数据库')
        target_path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(DB_PATH.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target_path)) as target:
            source.backup(target);target.execute('PRAGMA journal_mode=DELETE')
        print('快照完成：'+str(target_path))
    else:
        init_db()
        if args.command=='reset-password':
            from .main import password_hash
            password=getpass.getpass('输入新密码（至少10位）：')
            if password!=getpass.getpass('再次输入：'):raise SystemExit('两次输入不一致')
            hashed=password_hash(password)
            with db() as c:
                row=c.execute('SELECT id FROM users WHERE lower(username)=?',(args.username.lower(),)).fetchone()
                if not row:raise SystemExit('账号不存在')
                c.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,row['id']))
                c.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
                audit(c,None,'offline_password_reset',row['id'])
            print('密码已重置，其他数据未改变')
        else:
            with db() as c:
                print('数据库：'+str(DB_PATH))
                print('用户：'+str(c.execute('SELECT count(*) FROM users').fetchone()[0]))
                print('对局：'+str(c.execute('SELECT count(*) FROM records').fetchone()[0]))


if __name__=='__main__':main()
