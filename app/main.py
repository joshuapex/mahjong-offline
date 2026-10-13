import csv
import hashlib
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import bcrypt
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import ROOT, DB_PATH, TZ, RULE, db, init_db, now, setting, set_setting, audit, group_rows, regroup, rule_catalog, scoring_rule
from .stats import SEATS, records, summary, settle


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title='雀会 · 线下日麻战绩', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
secure_cookie = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def public_user(u):
    return {k:u[k] for k in ['id','username','nickname','role','active','can_record']}


def auth(request: Request):
    token = request.cookies.get('session', '')
    with db() as c:
        row = c.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1', (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    if not row:
        raise HTTPException(401, '请先登录')
    u = dict(row)
    if request.method not in ['GET','HEAD','OPTIONS'] and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''), u['csrf']):
        raise HTTPException(403, '请求校验失败，请刷新页面')
    return u


def admin(u=Depends(auth)):
    if u['role'] != 'admin':
        raise HTTPException(403, '仅管理员可操作')
    return u


def recorder(u=Depends(auth)):
    if u['role'] != 'admin' and not u['can_record']:
        raise HTTPException(403, '没有数据录入权限')
    return u


class Login(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=72)


def password_hash(password):
    if len(password) < 10 or len(password.encode()) > 72:
        raise HTTPException(422, '密码至少 10 个字符，UTF-8 长度不超过 72 字节')
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@app.get('/health')
def health():
    return {'status':'ok'}


@app.post('/api/login')
def login(data: Login, request: Request, response: Response):
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '请从本站登录')
    address = request.client.host if request.client else 'unknown'
    with db() as c:
        attempt = c.execute('SELECT * FROM login_attempts WHERE address=?', (address,)).fetchone()
        if attempt and time.time() - attempt['started'] < 300 and attempt['count'] >= 15:
            raise HTTPException(429, '登录尝试过多，请 5 分钟后重试')
        if not attempt or time.time() - attempt['started'] >= 300:
            c.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,0,?)', (address,time.time()))
        c.execute('UPDATE login_attempts SET count=count+1 WHERE address=?', (address,))
        row = c.execute('SELECT * FROM users WHERE lower(username)=? AND active=1', (data.username.strip().lower(),)).fetchone()
        valid = False
        if row:
            try:
                valid = bcrypt.checkpw(data.password.encode(), row['password_hash'].encode())
            except ValueError:
                pass
        # Commit failed attempts before raising, otherwise the transaction would roll back.
        if not valid:
            c.commit()
            raise HTTPException(401, '账号或密码错误')
        c.execute('DELETE FROM login_attempts WHERE address=?', (address,))
        c.execute('DELETE FROM sessions WHERE expires<=?', (now(),))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = (datetime.now(TZ)+timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'],csrf,expires))
    response.set_cookie('session', token, httponly=True, secure=secure_cookie, samesite='lax', max_age=604800)
    return {'user':public_user(row),'csrf':csrf}


@app.get('/api/me')
def me(u=Depends(auth)):
    return {'user':public_user(u),'csrf':u['csrf']}


@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(auth)):
    with db() as c:
        c.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(request.cookies['session'].encode()).hexdigest(),))
    response.delete_cookie('session')
    return {'ok':True}


class PasswordChange(BaseModel):
    old_password:str
    password:str


@app.post('/api/password')
def change_password(data:PasswordChange,u=Depends(auth)):
    try:
        valid = bcrypt.checkpw(data.old_password.encode(),u['password_hash'].encode())
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400,'原密码错误')
    hashed = password_hash(data.password)
    with db() as c:
        c.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,u['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
        audit(c,u['id'],'change_password',u['id'])
    return {'ok':True}


class Nickname(BaseModel):
    nickname:str = Field(min_length=1,max_length=30)


@app.put('/api/profile')
def profile(data:Nickname,u=Depends(auth)):
    nickname=data.nickname.strip()
    if not nickname:
        raise HTTPException(422,'昵称不能为空')
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE nickname=? AND id<>?',(nickname,u['id'])).fetchone():
            raise HTTPException(409,'昵称已使用')
        c.execute('UPDATE users SET nickname=? WHERE id=?',(nickname,u['id']))
        audit(c,u['id'],'nickname',u['id'],{'nickname':u['nickname']},{'nickname':nickname})
    return {'ok':True}


@app.get('/api/players')
def players(u=Depends(auth)):
    with db() as c:
        return [dict(r) for r in c.execute('SELECT id,nickname,username,active FROM users ORDER BY id')]


@app.get('/api/users')
def users(u=Depends(admin)):
    with db() as c:
        return [public_user(r) for r in c.execute('SELECT * FROM users ORDER BY id')]


class UserInput(BaseModel):
    username:str = ''
    nickname:str = Field(min_length=1,max_length=30)
    password:str = ''
    role:str = 'user'
    can_record:bool = False
    active:bool = True


def validate_user(c,data,uid=None):
    if data.role not in ['admin','user'] or not data.nickname.strip():
        raise HTTPException(422,'角色或昵称无效')
    if c.execute('SELECT 1 FROM users WHERE nickname=? AND id<>?', (data.nickname.strip(),uid or 0)).fetchone():
        raise HTTPException(409,'昵称已使用')


@app.post('/api/users')
def create_user(data:UserInput,u=Depends(admin)):
    login=data.username.strip().lower()
    if not re.fullmatch('[a-z]{2,40}',login):
        raise HTTPException(422,'账号须为 2～40 位英文字母')
    hashed=password_hash(data.password)
    with db() as c:
        validate_user(c,data)
        if c.execute('SELECT 1 FROM users WHERE lower(username)=?',(login,)).fetchone():
            raise HTTPException(409,'账号已存在')
        uid=c.execute('INSERT INTO users(username,nickname,password_hash,role,can_record,active,created_at) VALUES(?,?,?,?,?,?,?)',(login,data.nickname.strip(),hashed,data.role,data.can_record,data.active,now())).lastrowid
        audit(c,u['id'],'create_user',uid,after={'username':login,'nickname':data.nickname,'role':data.role,'can_record':data.can_record})
    return {'id':uid}


@app.put('/api/users/{uid}')
def update_user(uid:int,data:UserInput,u=Depends(admin)):
    with db() as c:
        old=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not old: raise HTTPException(404,'用户不存在')
        validate_user(c,data,uid)
        if old['role']=='admin' and (data.role!='admin' or not data.active) and c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
            raise HTTPException(409,'必须保留至少一个启用的管理员')
        c.execute('UPDATE users SET nickname=?,role=?,can_record=?,active=? WHERE id=?',(data.nickname.strip(),data.role,data.can_record,data.active,uid))
        if data.password:
            c.execute('UPDATE users SET password_hash=? WHERE id=?',(password_hash(data.password),uid))
        c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        audit(c,u['id'],'update_user',uid,public_user(old),{**data.model_dump(exclude={'password'}),'password_reset':bool(data.password)})
    return {'ok':True}


@app.delete('/api/users/{uid}')
def delete_user(uid:int,u=Depends(admin)):
    with db() as c:
        old=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not old: raise HTTPException(404,'用户不存在')
        if old['role']=='admin' and old['active'] and c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
            raise HTTPException(409,'不能移除最后一个管理员')
        associated=c.execute('SELECT 1 FROM records WHERE ? IN (east_user_id,south_user_id,west_user_id,north_user_id,created_by)',(uid,)).fetchone()
        if associated:
            c.execute('UPDATE users SET active=0 WHERE id=?',(uid,))
        else:
            c.execute('DELETE FROM users WHERE id=?',(uid,))
        c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        audit(c,u['id'],'disable_user' if associated else 'delete_user',uid,public_user(old))
    return {'disabled':bool(associated)}


class GameInput(BaseModel):
    user_ids:list[int]
    points:list[int]
    ended_at:str|None = None
    note:str = Field(default='',max_length=500)
    game_type:str = 'hanchan'
    round_id:int | None = None
    record_id:int | None = None


def game_values(c,data,existing=None):
    if len(data.user_ids)!=4 or len(set(data.user_ids))!=4 or len(data.points)!=4:
        raise HTTPException(422,'请选择四名不同玩家并填写四个点数')
    if data.game_type not in ['hanchan','tonpuu']:
        raise HTTPException(422,'场次类型无效')
    rule=json.loads(existing['rules_json']) if existing else RULE
    if sum(data.points)!=rule['starting']*4:
        raise HTTPException(422,f"四人点棒合计应为 {rule['starting']*4}，请先明确剩余供托归属")
    if any(p%100!=0 or abs(p)>1000000 for p in data.points):
        raise HTTPException(422,'点棒须为 100 的整数倍且在合理范围内')
    try:
        stamp=datetime.fromisoformat(data.ended_at or (existing['created_at'] if existing else now()))
        if stamp.tzinfo: stamp=stamp.astimezone(TZ).replace(tzinfo=None)
        if stamp>datetime.now(TZ).replace(tzinfo=None)+timedelta(minutes=5):
            raise ValueError()
    except ValueError:
        raise HTTPException(422,'实际结束时间无效或在未来')
    for uid in data.user_ids:
        found=c.execute('SELECT active FROM users WHERE id=?',(uid,)).fetchone()
        previous_players=[existing[f'{s}_user_id'] for s in SEATS] if existing else []
        if not found or (not found['active'] and uid not in previous_players):
            raise HTTPException(422,'不能选择不存在或已停用的玩家')
    return stamp.strftime('%Y-%m-%d %H:%M:%S'),rule


def choose_round(c,stamp,requested=None,exclude=None):
    if requested:
        if not c.execute('SELECT 1 FROM rounds WHERE id=?',(requested,)).fetchone():
            raise HTTPException(422,'届次不存在')
        return requested
    neighbors=c.execute("SELECT round_id,created_at FROM records WHERE status='active' AND id<>? ORDER BY created_at",(exclude or 0,)).fetchall()
    t=datetime.fromisoformat(stamp)
    matches={r['round_id'] for r in neighbors if r['round_id'] and abs((datetime.fromisoformat(r['created_at'])-t).total_seconds())<7200}
    if len(matches)>1:
        raise HTTPException(409,'此时间连接了两个已有届次，请管理员先确认合并或选择届次')
    if matches: return matches.pop()
    return c.execute('INSERT INTO rounds(number) VALUES((SELECT coalesce(max(number),0)+1 FROM rounds))').lastrowid


@app.post('/api/records/preview')
def preview(data:GameInput,u=Depends(recorder)):
    with db() as c:
        old=c.execute("SELECT * FROM records WHERE id=? AND status='active'",(data.record_id,)).fetchone() if data.record_id else None
        if data.record_id and (not old or u['role']!='admin' and old['created_by']!=u['id']):
            raise HTTPException(403,'不能预览他人修改')
        stamp,rule=game_values(c,data,old)
        rule=scoring_rule(c)
    row={'rules_json':json.dumps(rule),**{f'{s}_user_id':data.user_ids[i] for i,s in enumerate(SEATS)},**{f'{s}_score':data.points[i] for i,s in enumerate(SEATS)}}
    return {'results':settle(row),'rules':rule}


@app.post('/api/records')
def create_record(data:GameInput,request:Request,u=Depends(recorder)):
    # A draft-specific key makes a repeated mobile submission idempotent.
    key=request.headers.get('Idempotency-Key','')
    if not re.fullmatch('[A-Za-z0-9-]{10,80}',key): raise HTTPException(422,'缺少有效提交标识')
    fingerprint=hashlib.sha256(json.dumps(data.model_dump(),sort_keys=True).encode()).hexdigest()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        saved=setting(c,'submit:'+str(u['id'])+':'+key)
        if saved:
            if saved['hash']!=fingerprint: raise HTTPException(409,'提交标识对应的数据已变化，请新建草稿')
            return {'id':saved['id'],'repeated':True}
        stamp,rule=game_values(c,data)
        if data.round_id and u['role']!='admin': raise HTTPException(403,'只有管理员可以指定届次')
        rid=choose_round(c,stamp,data.round_id)
        values=data.user_ids+data.points+[data.note,u['id'],stamp,now(),now(),rid,json.dumps(rule),data.game_type]
        recid=c.execute('INSERT INTO records(east_user_id,south_user_id,west_user_id,north_user_id,east_score,south_score,west_score,north_score,note,created_by,created_at,submitted_at,updated_at,round_id,rules_json,game_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values).lastrowid
        set_setting(c,'submit:'+str(u['id'])+':'+key,{'id':recid,'hash':fingerprint})
        audit(c,u['id'],'create_record',recid,after=data.model_dump())
    return {'id':recid}


@app.put('/api/records/{rid}')
def edit_record(rid:int,data:GameInput,u=Depends(recorder)):
    with db() as c:
        old=c.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
        if not old or old['status']!='active': raise HTTPException(404,'有效记录不存在')
        if u['role']!='admin' and old['created_by']!=u['id']: raise HTTPException(403,'只能修改自己录入的记录')
        stamp,rule=game_values(c,data,old)
        if u['role']!='admin' and data.round_id not in [None,old['round_id']]: raise HTTPException(403,'只有管理员可以调整届次')
        # Keep published round IDs stable; administrator can explicitly regroup after date corrections.
        round_id=data.round_id if data.round_id else old['round_id']
        if round_id and not c.execute('SELECT 1 FROM rounds WHERE id=?',(round_id,)).fetchone(): raise HTTPException(422,'届次不存在')
        c.execute('UPDATE records SET east_user_id=?,south_user_id=?,west_user_id=?,north_user_id=?,east_score=?,south_score=?,west_score=?,north_score=?,note=?,created_at=?,updated_at=?,game_type=?,round_id=? WHERE id=?',data.user_ids+data.points+[data.note,stamp,now(),data.game_type,round_id,rid])
        audit(c,u['id'],'edit_record',rid,dict(old),data.model_dump())
    return {'ok':True,'round_review':stamp!=old['created_at']}


@app.delete('/api/records/{rid}')
def void_record(rid:int,u=Depends(admin)):
    with db() as c:
        old=c.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
        if not old: raise HTTPException(404,'记录不存在')
        c.execute("UPDATE records SET status='void',updated_at=? WHERE id=?",(now(),rid))
        audit(c,u['id'],'void_record',rid,dict(old))
    return {'ok':True}


@app.get('/api/records/{rid}')
def record_detail(rid:int,u=Depends(auth)):
    with db() as c:
        row=c.execute("SELECT * FROM records WHERE id=? AND status='active'",(rid,)).fetchone()
        if not row: raise HTTPException(404,'记录不存在')
        result=dict(row); result['scoring_rule']=scoring_rule(c); result['results']=settle(result,result['scoring_rule'])
        return result


@app.get('/api/rounds')
def rounds(u=Depends(auth)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT d.id,d.number,count(r.id) count,min(r.created_at) start,max(r.created_at) end FROM rounds d JOIN records r ON r.round_id=d.id AND r.status='active' GROUP BY d.id ORDER BY d.number DESC")]


@app.get('/api/stats')
def stats(round_id:int|None=None,user_id:int|None=None,start:str|None=None,end:str|None=None,game_type:str|None=None,u=Depends(auth)):
    for date in [start,end]:
        if date:
            try: datetime.strptime(date,'%Y-%m-%d')
            except ValueError: raise HTTPException(422,'日期格式无效')
    with db() as c:
        return summary(c,records(c,round_id,None,start,end,game_type),user_id,scoring_rule(c))


@app.get('/api/rules')
@app.get('/api/rule-versions')
def rule_versions(u=Depends(auth)):
    with db() as c:
        return rule_catalog(c)


@app.get('/api/admin/regroup')
def regroup_preview(u=Depends(admin)):
    with db() as c:
        rows=[dict(r) for r in c.execute("SELECT id,created_at FROM records WHERE status='active'")]
        digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
        return {'groups':group_rows(rows),'token':digest}


class RegroupInput(BaseModel):
    token:str


class AssignRoundInput(BaseModel):
    record_ids:list[int]
    round_id:int|None=None


@app.post('/api/admin/rounds/assign')
def assign_round(data:AssignRoundInput,u=Depends(admin)):
    if not data.record_ids or len(data.record_ids)>1000 or len(set(data.record_ids))!=len(data.record_ids):
        raise HTTPException(422,'请选择有效且不重复的记录 ID')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        before=[]
        for rid in data.record_ids:
            row=c.execute("SELECT id,round_id FROM records WHERE id=? AND status='active'",(rid,)).fetchone()
            if not row:raise HTTPException(422,f'有效记录 {rid} 不存在')
            before.append(dict(row))
        round_id=data.round_id
        if round_id:
            if not c.execute('SELECT 1 FROM rounds WHERE id=?',(round_id,)).fetchone():raise HTTPException(422,'届次不存在')
        else:
            round_id=c.execute('INSERT INTO rounds(number) VALUES((SELECT coalesce(max(number),0)+1 FROM rounds))').lastrowid
        c.executemany('UPDATE records SET round_id=?,updated_at=? WHERE id=?',[(round_id,now(),rid) for rid in data.record_ids])
        audit(c,u['id'],'assign_round',round_id,before,{'records':data.record_ids,'round_id':round_id})
    return {'round_id':round_id}


@app.post('/api/admin/regroup')
def regroup_apply(data:RegroupInput,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows=[dict(r) for r in c.execute("SELECT id,created_at FROM records WHERE status='active'")]
        digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
        if data.token!=digest: raise HTTPException(409,'预览后数据已变化，请重新预览')
        groups=regroup(c)
        audit(c,u['id'],'regroup','all',after=groups)
    return {'groups':groups}


@app.get('/api/admin/audit')
def audit_list(u=Depends(admin)):
    with db() as c:
        return [dict(r) for r in c.execute('SELECT a.*,u.nickname actor_name FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 100')]


class LLMSettings(BaseModel):
    base_url:str = Field(max_length=300)
    model:str = Field(min_length=1,max_length=150)
    api_key:str = Field(default='',max_length=500)
    clear_key:bool = False


def validated_base(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422,'API 地址须为 HTTPS 地址，例如 https://供应商/v1')
    return url.rstrip('/')


@app.get('/api/admin/llm')
def llm_settings(u=Depends(admin)):
    with db() as c:
        cfg=setting(c,'llm',{})
        return {'base_url':cfg.get('base_url',''),'model':cfg.get('model',''),'has_key':bool(cfg.get('api_key'))}


@app.put('/api/admin/llm')
def llm_save(data:LLMSettings,u=Depends(admin)):
    base=validated_base(data.base_url)
    with db() as c:
        old=setting(c,'llm',{})
        cfg={'base_url':base,'model':data.model.strip(),'api_key':('' if data.clear_key else data.api_key or old.get('api_key',''))}
        set_setting(c,'llm',cfg)
        audit(c,u['id'],'llm_config','llm',after={'base_url':base,'model':cfg['model'],'has_key':bool(cfg['api_key'])})
    return {'ok':True}


def replace_names(text,c):
    for r in c.execute('SELECT id,nickname,username FROM users ORDER BY id DESC'):
        text=text.replace('[[player:'+str(r['id'])+']]',r['nickname'] or r['username'])
    return text


def analysis_scope(c,scope,target):
    if scope not in ['overall','round','player']: raise HTTPException(422,'分析范围无效')
    if scope=='round' and not c.execute('SELECT 1 FROM rounds WHERE id=?',(target,)).fetchone(): raise HTTPException(404,'届次不存在')
    if scope=='player' and not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): raise HTTPException(404,'玩家不存在')
    return summary(c,records(c,round_id=target if scope=='round' else None,user_id=target if scope=='player' else None),target if scope=='player' else None,scoring_rule(c))


def run_analysis(jobid,scope,target,cfg,snapshot):
    try:
        compact={'scope':scope,'games':snapshot['count'],'scoring_rule':snapshot['scoring_rule'],'players':[]}
        for p in snapshot['players']:
            item={k:v for k,v in p.items() if k not in ['nickname','trend']}
            if 'co_players' in item.get('details',{}):
                item['details']={**item['details'],'co_players':[{**{k:v for k,v in peer.items() if k!='nickname'},'player':'[[player:'+str(peer['user_id'])+']]'} for peer in item['details']['co_players']]}
            item['player']='[[player:'+str(p['user_id'])+']]'
            item['recent_trend']=p['trend'][-20:]
            compact['players'].append(item)
        prompt='你是线下四人日麻成绩分析员。只用提供的统计事实，中文输出，400字以内。按总体表现、近期变化、样本限制三部分写。玩家必须使用给定的 [[player:ID]] 标记，不创造名字。不得推断和牌率、放铳率、立直率、具体打法或因果。同桌比较只描述相关差异；无未同桌样本时不可作差异结论，其他同桌成员和时段可能不同。不得将不同长度的样本当作确定实力结论。无数据时明确说明。不要使用HTML。'
        with httpx.Client(timeout=httpx.Timeout(120,connect=15),follow_redirects=False,trust_env=False) as client:
            res=client.post(cfg['base_url']+'/chat/completions',headers={'Authorization':'Bearer '+cfg['api_key']},json={'model':cfg['model'],'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps(compact,ensure_ascii=False)}],'max_tokens':1600})
            res.raise_for_status()
            content=res.json()['choices'][0]['message']['content']
            if not isinstance(content,str) or not content.strip(): raise ValueError('empty model response')
        with db() as c:
            c.execute('INSERT INTO analyses(scope,target,data_hash,payload,model,created_at,rule_id,rule_json) VALUES(?,?,?,?,?,?,?,?)',(scope,target,snapshot['data_hash'],content[:12000],cfg['model'],now(),snapshot['scoring_rule']['version'],json.dumps(snapshot['scoring_rule'])))
            c.execute("UPDATE jobs SET status='done',finished_at=? WHERE id=?",(now(),jobid))
    except Exception:
        # Provider errors may contain authorization or request data; never expose them.
        with db() as c:
            c.execute("UPDATE jobs SET status='failed',error='调用失败，请检查 API 配置、额度和网络后重试',finished_at=? WHERE id=?",(now(),jobid))


class AnalysisInput(BaseModel):
    scope:str
    target:int = 0


@app.get('/api/analysis')
def analysis(scope:str='overall',target:int=0,u=Depends(auth)):
    with db() as c:
        data=analysis_scope(c,scope,target)
        row=c.execute('SELECT * FROM analyses WHERE scope=? AND target=? AND rule_id=? ORDER BY id DESC LIMIT 1',(scope,target,data['scoring_rule']['version'])).fetchone()
        if not row: return {'analysis':None}
        item=dict(row); item['text']=replace_names(item.pop('payload'),c); item['stale']=item['data_hash']!=data['data_hash']
        return {'analysis':item}


@app.post('/api/analysis')
def generate_analysis(data:AnalysisInput,u=Depends(recorder)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        snapshot=analysis_scope(c,data.scope,data.target)
        if not snapshot['count']: raise HTTPException(422,'该范围没有有效对局')
        cfg=setting(c,'llm',{})
        if not all(cfg.get(k) for k in ['base_url','model','api_key']): raise HTTPException(422,'请管理员先配置 LLM')
        if c.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone(): raise HTTPException(409,'已有分析任务生成中，请稍后再试')
        jobid=secrets.token_hex(16)
        c.execute("INSERT INTO jobs(id,status,created_at) VALUES(?,'running',?)",(jobid,now()))
        audit(c,u['id'],'generate_analysis',data.scope+':'+str(data.target))
    threading.Thread(target=run_analysis,args=(jobid,data.scope,data.target,cfg,snapshot),daemon=True).start()
    return {'job_id':jobid}


@app.get('/api/jobs/{jobid}')
def job(jobid:str,u=Depends(auth)):
    with db() as c:
        row=c.execute('SELECT id,status,error,created_at,finished_at FROM jobs WHERE id=?',(jobid,)).fetchone()
        if not row: raise HTTPException(404,'任务不存在')
        return dict(row)


class AccessSettings(BaseModel):
    protocol:str='https'
    external_port:int=Field(default=443,ge=1,le=65535)
    public_host:str=Field(default='',max_length=253)


@app.get('/api/admin/access')
def access_settings(u=Depends(admin)):
    with db() as c: return setting(c,'access',{'protocol':'https','external_port':443,'public_host':''})


@app.put('/api/admin/access')
def save_access(data:AccessSettings,u=Depends(admin)):
    host=data.public_host.strip()
    if data.protocol not in ['https','http'] or (host and not re.fullmatch('[A-Za-z0-9.-]+',host)):
        raise HTTPException(422,'协议或域名无效；域名不包含协议、路径或端口')
    with db() as c:
        set_setting(c,'access',{**data.model_dump(),'public_host':host})
        audit(c,u['id'],'access_config','access',after=data.model_dump())
    return {'ok':True}


@app.get('/api/admin/access-qr')
def access_qr(u=Depends(admin)):
    import qrcode
    import qrcode.image.svg
    with db() as c:
        cfg=setting(c,'access',{'protocol':'https','external_port':443,'public_host':''})
        last=setting(c,'last_ip',{})
    if not last: raise HTTPException(422,'请先查询公网 IP')
    url=f"{cfg['protocol']}://{cfg.get('public_host') or last['ip']}:{cfg['external_port']}"
    out=io.BytesIO()
    qrcode.make(url,image_factory=qrcode.image.svg.SvgPathImage).save(out)
    return Response(out.getvalue(),media_type='image/svg+xml')


class ImportInput(BaseModel):
    csv:str=Field(max_length=1000000)
    token:str=''


def parse_import(c,data):
    digest=hashlib.sha256(data.csv.encode()).hexdigest()
    rows=[];errors=[]
    try:
        reader=csv.DictReader(io.StringIO(data.csv.lstrip('\ufeff')))
        required={'ended_at'}|{f'{s}_{x}' for s in SEATS for x in ['user_id','score']}
        if not required.issubset(set(reader.fieldnames or [])):
            return [],['列名不完整，请使用导入模板'],digest
        for n,row in enumerate(reader,2):
            if n>1001:
                errors.append('单次最多导入 1000 场');break
            try:
                item=GameInput(user_ids=[int(row[f'{s}_user_id']) for s in SEATS],points=[int(row[f'{s}_score']) for s in SEATS],ended_at=row['ended_at'],note=row.get('note') or '',game_type=row.get('game_type') or 'hanchan')
                stamp,rule=game_values(c,item)
                rows.append((item,stamp,rule))
            except HTTPException as e: errors.append(f'第 {n} 行：{e.detail}')
            except (ValueError,TypeError): errors.append(f'第 {n} 行：格式错误')
    except csv.Error: errors.append('CSV 格式无效')
    if not rows and not errors:errors.append('文件没有对局数据')
    return rows,errors,digest


@app.post('/api/admin/import-preview')
def import_preview(data:ImportInput,u=Depends(admin)):
    with db() as c:
        rows,errors,digest=parse_import(c,data)
        return {'count':len(rows),'errors':errors,'token':digest,'already_imported':bool(setting(c,'import:'+digest))}


@app.post('/api/admin/import')
def import_apply(data:ImportInput,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows,errors,digest=parse_import(c,data)
        if errors:raise HTTPException(422,'文件仍有错误，请重新预览')
        if digest!=data.token:raise HTTPException(409,'文件内容已改变，请重新预览')
        if setting(c,'import:'+digest):raise HTTPException(409,'此文件已导入')
        ids=[]
        for item,stamp,rule in sorted(rows,key=lambda r:r[1]):
            round_id=choose_round(c,stamp)
            values=item.user_ids+item.points+[item.note,u['id'],stamp,now(),now(),round_id,json.dumps(rule),item.game_type]
            rid=c.execute('INSERT INTO records(east_user_id,south_user_id,west_user_id,north_user_id,east_score,south_score,west_score,north_score,note,created_by,created_at,submitted_at,updated_at,round_id,rules_json,game_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values).lastrowid
            ids.append(rid)
        set_setting(c,'import:'+digest,ids)
        audit(c,u['id'],'import_records',digest,after={'count':len(ids),'ids':ids})
    return {'count':len(ids)}


@app.post('/api/admin/public-ip')
def public_ip(u=Depends(admin)):
    detected=None
    for endpoint in ['https://api.ipify.org','https://checkip.amazonaws.com']:
        try:
            with httpx.Client(timeout=8,trust_env=False) as client:
                res=client.get(endpoint);res.raise_for_status()
                ip=ipaddress.ip_address(res.text.strip())
                if ip.version==4 and ip.is_global:
                    detected=str(ip);break
        except Exception: continue
    if not detected: raise HTTPException(502,'公网 IP 查询失败，请检查 NAS 出站网络')
    with db() as c:
        cfg=setting(c,'access',{'protocol':'https','external_port':443,'public_host':''})
        previous=setting(c,'last_ip',{})
        current={'ip':detected,'checked_at':now()}
        set_setting(c,'last_ip',current)
    host=cfg.get('public_host') or detected
    return {**current,'changed':bool(previous and previous['ip']!=detected),'url':f"{cfg['protocol']}://{host}:{cfg['external_port']}",'verified':False}


@app.get('/api/admin/rule')
def get_rule(u=Depends(admin)):
    with db() as c: return scoring_rule(c)

class RuleNameInput(BaseModel):
    name:str=Field(min_length=1,max_length=40)

class RuleInput(BaseModel):
    starting:int
    returning:int
    uma:list[int]
    name:str=Field(default='',max_length=40)

def rule_values(data,version,catalog):
    if data.starting!=25000 or data.returning<data.starting or data.returning%100 or len(data.uma)!=4 or sum(data.uma)!=0:
        raise HTTPException(422,'实际起始点棒固定为25000；返还点须为百点整数且不小于起始，四个 Uma 合计为零')
    if (data.returning-data.starting)*4%1000: raise HTTPException(422,'当前规则要求 Oka 为整数成绩')
    name=data.name.strip() or f'四麻规则 {version}'
    if any(r['name']==name and r['version']!=version for r in catalog): raise HTTPException(409,'规则名称已使用')
    return {**data.model_dump(),'name':name,'oka':(data.returning-data.starting)*4//1000,'tie':'initial_seat','version':version}

@app.post('/api/admin/rules')
def create_rule(data:RuleInput,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        catalog=rule_catalog(c);version=setting(c,'next_rule_id',max(r['version'] for r in catalog)+1)
        new={**rule_values(data,version,catalog),'current':False}
        catalog.append(new);set_setting(c,'scoring_profiles',catalog);set_setting(c,'next_rule_id',version+1)
        audit(c,u['id'],'create_rule',version,None,new)
    return new

@app.put('/api/admin/rules/{version}')
def edit_rule(version:int,data:RuleInput,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=scoring_rule(c,version);catalog=rule_catalog(c)
        new={**rule_values(data,version,catalog),'current':old['current']}
        set_setting(c,'scoring_profiles',[new if r['version']==version else r for r in catalog])
        audit(c,u['id'],'edit_rule',version,old,new)
    return new

@app.put('/api/admin/rules/{version}/name')
def rename_rule(version:int,data:RuleNameInput,u=Depends(admin)):
    if not data.name.strip(): raise HTTPException(422,'规则名称不能为空')
    with db() as c: old=scoring_rule(c,version)
    return edit_rule(version,RuleInput(**{k:old[k] for k in ['starting','returning','uma']},name=data.name),u)

@app.post('/api/admin/rules/{version}/default')
def default_rule(version:int,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE');scoring_rule(c,version)
        catalog=[{**r,'current':r['version']==version} for r in rule_catalog(c)]
        set_setting(c,'scoring_profiles',catalog);audit(c,u['id'],'default_rule',version)
    return {'ok':True}

@app.delete('/api/admin/rules/{version}')
def delete_rule(version:int,u=Depends(admin)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE');old=scoring_rule(c,version);catalog=rule_catalog(c)
        if len(catalog)==1: raise HTTPException(409,'至少保留一套默认结算规则')
        catalog=[r for r in catalog if r['version']!=version]
        if old['current']: catalog[0]['current']=True
        set_setting(c,'scoring_profiles',catalog);audit(c,u['id'],'delete_rule',version,old)
    return {'ok':True,'default':next(r['version'] for r in catalog if r['current'])}

@app.put('/api/admin/rule')
def save_rule(data:RuleInput,u=Depends(admin)):
    new=create_rule(data,u);default_rule(new['version'],u)
    return new


@app.get('/api/export')
def export(u=Depends(auth)):
    with db() as c:
        data=summary(c,records(c),rule=scoring_rule(c))
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['记录ID','届次','结束时间','类型','玩家ID','昵称','开局座次','终局点棒','顺位','结算成绩','统计口径'])
    for g in reversed(data['games']):
        for p in g['results']:
            nickname=p['nickname']
            if nickname.startswith(('=','+','-','@','\t','\r')): nickname="'"+nickname
            writer.writerow([g['id'],g['round_number'],g['created_at'],g['game_type'],p['user_id'],nickname,p['seat'],p['points'],p['rank'],p['score'],"'"+data['scoring_rule']['name']])
    return Response(content=('\ufeff'+out.getvalue()).encode(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=mahjong-records.csv'})


@app.post('/api/admin/backup')
def backup(u=Depends(admin)):
    folder=DB_PATH.parent/'backups';folder.mkdir(exist_ok=True)
    dest=folder/('backup-'+datetime.now(TZ).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.db')
    with db() as c:
        audit(c,u['id'],'backup',dest.name)
    with closing(sqlite3.connect(DB_PATH)) as source,closing(sqlite3.connect(dest)) as target:
        source.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('DELETE FROM sessions');target.execute('DELETE FROM login_attempts');target.commit()
    return FileResponse(dest,media_type='application/octet-stream',filename=dest.name)


app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')
