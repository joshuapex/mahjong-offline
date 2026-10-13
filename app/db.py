import json
import os
import re
import sqlite3
from pypinyin import lazy_pinyin
from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get('APP_DB', str(ROOT / 'data/app.db')))
TZ = timezone(timedelta(hours=8))
RULE = {'starting': 25000, 'returning': 30000, 'uma': [15, 5, -5, -15], 'oka': 20, 'tie': 'initial_seat', 'version': 1}


def now():
    return datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def group_rows(rows):
    groups = []
    previous = None
    for row in sorted(rows, key=lambda r: (r['created_at'], r['id'])):
        t = datetime.fromisoformat(row['created_at'])
        if previous is None or (t - previous).total_seconds() >= 7200:
            groups.append([])
        groups[-1].append(row['id'])
        previous = t
    return groups


def regroup(con):
    rows = [dict(r) for r in con.execute("SELECT id,created_at FROM records WHERE status='active'")]
    groups = group_rows(rows)
    con.execute('UPDATE records SET round_id=NULL')
    con.execute('DELETE FROM rounds')
    for n, ids in enumerate(groups, 1):
        rid = con.execute('INSERT INTO rounds(number) VALUES(?)', (n,)).lastrowid
        con.executemany('UPDATE records SET round_id=? WHERE id=?', [(rid, i) for i in ids])
    return groups


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    original = DB_PATH.parent / 'mahjong.db'
    if not DB_PATH.exists() and original.exists():
        # SQLite backup includes committed WAL state; never copy only the main file.
        with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(DB_PATH)) as target:
                source.backup(target)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
          nickname TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          east_user_id INTEGER NOT NULL REFERENCES users(id), south_user_id INTEGER NOT NULL REFERENCES users(id),
          west_user_id INTEGER NOT NULL REFERENCES users(id), north_user_id INTEGER NOT NULL REFERENCES users(id),
          east_score INTEGER NOT NULL, south_score INTEGER NOT NULL, west_score INTEGER NOT NULL, north_score INTEGER NOT NULL,
          note TEXT, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY AUTOINCREMENT,number INTEGER NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor INTEGER,action TEXT NOT NULL,target TEXT,before_json TEXT,after_json TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,target INTEGER NOT NULL DEFAULT 0,data_hash TEXT NOT NULL,payload TEXT NOT NULL,model TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,finished_at TEXT);
        CREATE TABLE IF NOT EXISTS login_attempts(address TEXT PRIMARY KEY,count INTEGER NOT NULL,started REAL NOT NULL);
        ''')
        for table, additions in {
            'users': {'active': 'INTEGER NOT NULL DEFAULT 1', 'can_record': 'INTEGER NOT NULL DEFAULT 0', 'legacy_username': 'TEXT'},
            'analyses': {'rule_id': 'INTEGER NOT NULL DEFAULT 1', 'rule_json': 'TEXT', 'analysis_version': 'INTEGER NOT NULL DEFAULT 1'},
            'records': {'status': "TEXT NOT NULL DEFAULT 'active'", 'submitted_at': 'TEXT', 'updated_at': 'TEXT', 'round_id': 'INTEGER REFERENCES rounds(id)', 'rules_json': 'TEXT', 'game_type': "TEXT NOT NULL DEFAULT 'hanchan'"},
        }.items():
            existing = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, definition in additions.items():
                if name not in existing:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        if not c.execute("SELECT 1 FROM settings WHERE key='migration_v1'").fetchone():
            for u in c.execute('SELECT id,username FROM users').fetchall():
                if not re.fullmatch('[A-Za-z]+', u['username']):
                    login = ''.join(lazy_pinyin(u['username'])).lower()
                    if not re.fullmatch('[a-z]{2,40}', login):
                        raise RuntimeError('历史账号无法转换为英文拼音，请先在数据库中调整该账号')
                    if c.execute('SELECT 1 FROM users WHERE lower(username)=? AND id<>?', (login,u['id'])).fetchone():
                        raise RuntimeError('历史账号拼音发生冲突，请先在数据库中调整重名账号')
                    c.execute('UPDATE users SET legacy_username=username,username=? WHERE id=?', (login, u['id']))
                else:
                    c.execute('UPDATE users SET username=? WHERE id=?', (u['username'].lower(), u['id']))
            c.execute("UPDATE users SET can_record=1 WHERE role='admin'")
            c.execute('UPDATE records SET rules_json=? WHERE rules_json IS NULL', (json.dumps(RULE),))
            regroup(c)
            c.execute("INSERT INTO settings VALUES('migration_v1','1')")
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_login_ci ON users(lower(username))')
        c.execute('CREATE INDEX IF NOT EXISTS records_time ON records(created_at,id)')
        c.execute('CREATE INDEX IF NOT EXISTS records_round ON records(round_id)')
        c.execute('CREATE INDEX IF NOT EXISTS analyses_scope ON analyses(scope,target,id)')
        c.execute("UPDATE jobs SET status='failed',error='服务重启中断了任务' WHERE status='running'")
        c.execute("INSERT OR IGNORE INTO settings VALUES('rule',?)", (json.dumps(RULE),))
        if setting(c,'scoring_profiles') is None:
            profiles=legacy_rule_catalog(c)
            set_setting(c,'scoring_profiles',profiles)
            set_setting(c,'next_rule_id',max(r['version'] for r in profiles)+1)
        if not c.execute('SELECT 1 FROM users').fetchone():
            import bcrypt
            login = os.environ.get('ADMIN_USERNAME', 'admin').lower()
            password = os.environ.get('ADMIN_PASSWORD', '')
            if not re.fullmatch('[a-z]+', login) or len(password) < 10:
                raise RuntimeError('空数据库需配置英文 ADMIN_USERNAME 和至少 10 位 ADMIN_PASSWORD')
            c.execute("INSERT INTO users(username,password_hash,role,nickname,created_at,can_record) VALUES(?,?,'admin','管理员',?,1)",
                      (login, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(), now()))


def setting(c, key, default=None):
    row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def set_setting(c, key, value):
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))


def legacy_rule_catalog(c):
    names=setting(c,'rule_names',{})
    default=setting(c,'rule',RULE)
    catalog={int(v):{**r,'count':0} for v,r in setting(c,'rule_definitions',{}).items()}
    for row in c.execute("SELECT rules_json,sum(CASE WHEN status='active' THEN 1 ELSE 0 END) count FROM records GROUP BY rules_json"):
        rule=json.loads(row['rules_json']);version=rule['version']
        catalog.setdefault(version,{**rule,'count':0})['count']+=row['count']
    catalog.setdefault(default['version'],{**default,'count':0})
    return [{**r,'name':names.get(str(v),'默认四麻' if v==1 else f'四麻规则 {v}'),'current':v==default['version']} for v,r in sorted(catalog.items())]


def audit(c, actor, action, target, before=None, after=None):
    c.execute('INSERT INTO audit(actor,action,target,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)',
              (actor, action, str(target), json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False) if after else None, now()))


def rule_catalog(c):
    return [{k:v for k,v in r.items() if k!='count'} for r in (setting(c,'scoring_profiles',None) or legacy_rule_catalog(c))]

def scoring_rule(c, rule_id=None):
    catalog=rule_catalog(c)
    rule=next((r for r in catalog if r['version']==rule_id),None) if rule_id is not None else next(r for r in catalog if r['current'])
    if rule is None:
        from fastapi import HTTPException
        raise HTTPException(404,'结算规则不存在，请重新选择')
    return rule
