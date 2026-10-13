"""Isolated browser QA server: never rewrites production passwords."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import sys

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
folder=root/'test-output'
folder.mkdir(exist_ok=True)
from tests.fixtures import seed
source_path=folder/'synthetic-seed.db'
# Keep existing database files untouched; create isolated synthetic preview files.
if not source_path.exists():seed(source_path)
db_path=folder/'synthetic-preview.db'
with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(db_path)) as target:source.backup(target)
os.environ['APP_DB']=str(db_path)
os.environ['COOKIE_SECURE']='false'
from app.db import init_db,db
import bcrypt
init_db()
with db() as c:
    c.execute('UPDATE users SET password_hash=?',(bcrypt.hashpw(b'Preview-only-2026',bcrypt.gensalt(rounds=4)).decode(),))
if __name__=='__main__':
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=8765)
