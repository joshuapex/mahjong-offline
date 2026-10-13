"""Synthetic test data only. Never read a user's database for automated tests."""
import sqlite3
from contextlib import closing


def seed(path):
    with closing(sqlite3.connect(path)) as c:
        c.executescript('''
        CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL,nickname TEXT,created_at TEXT NOT NULL);
        CREATE TABLE records(id INTEGER PRIMARY KEY AUTOINCREMENT,east_user_id INTEGER NOT NULL REFERENCES users(id),south_user_id INTEGER NOT NULL REFERENCES users(id),west_user_id INTEGER NOT NULL REFERENCES users(id),north_user_id INTEGER NOT NULL REFERENCES users(id),east_score INTEGER NOT NULL,south_score INTEGER NOT NULL,west_score INTEGER NOT NULL,north_score INTEGER NOT NULL,note TEXT,created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL);
        ''')
        for i,char in enumerate(['甲','乙','丙','丁','戊'],1):
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(i,'林'+char,'not-a-real-password','admin' if i==1 else 'user','虚构玩家'+char,'2026-01-01 00:00:00'))
        times=['2026-01-01 21:30:00','2026-01-01 22:30:00','2026-01-01 23:30:00','2026-01-02 00:30:00','2026-01-02 01:30:00','2026-01-02 02:30:00','2026-02-01 20:30:00','2026-02-01 21:30:00','2026-02-01 22:30:00','2026-02-01 23:30:00','2026-02-02 00:30:00']
        for i,t in enumerate(times,1):
            ids=[1,2,3,4 if i<=6 else 5]
            points=[40000,30000,20000,10000] if i%2 else [20000,40000,30000,10000]
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[i]+ids+points+['虚构测试场次',1,t])
        c.commit()
