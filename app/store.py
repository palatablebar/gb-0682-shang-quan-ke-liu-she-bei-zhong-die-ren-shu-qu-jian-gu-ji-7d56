import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 封存版本只增不改：同一批次同一证据摘要只允许一个版本。
    db.execute('''CREATE TABLE IF NOT EXISTS seals (
        run_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(run_id,version), UNIQUE(run_id,digest))''')
    return db

def save(run_id, body, update=False):
    with connection() as db:
        if update:
            db.execute('INSERT INTO runs(id,body) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                (run_id,json.dumps(body,ensure_ascii=False)))
        else:
            db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return view(run_id)


def seal(run_id, body, digest):
    """登记封存版本（单事务）。同一批次的同一证据摘要复用已有版本，不新增。"""
    body=dict(body);body['run_id']=run_id
    return record(body,digest)


def seals(run_id):
    with connection() as db:
        rows=db.execute('SELECT version,digest,created_at FROM seals WHERE run_id=? ORDER BY version',(run_id,)).fetchall()
    published=published_digest(run_id)
    return [dict(r,published=r['digest']==published) for r in rows]


def sealed(run_id, version=None):
    """取封存证据。默认取已发布版本，未发布时取最新封存版本。"""
    with connection() as db:
        if version is None:
            digest=published_digest(run_id)
            if digest is None:
                row=db.execute('SELECT * FROM seals WHERE run_id=? ORDER BY version DESC LIMIT 1',(run_id,)).fetchone()
            else:
                row=db.execute('SELECT * FROM seals WHERE run_id=? AND digest=?',(run_id,digest)).fetchone()
        else:
            row=db.execute('SELECT * FROM seals WHERE run_id=? AND version=?',(run_id,version)).fetchone()
    if not row:raise DomainError('NOT_FOUND','封存证据不存在',404)
    body=json.loads(row['body'])
    body.update(run_id=row['run_id'],seal_version=row['version'],seal_digest=row['digest'],sealed_at=row['created_at'])
    return body


def view(run_id):
    """对外视图：有已发布封存版本时以它为准，否则取最近一次记录。

    发布失败的新证据只存在于 seals 表，不会改变这里看到的结论。
    """
    digest=published_digest(run_id)
    if digest is not None:
        body=sealed(run_id)
        body.update(_review_state(run_id))
        return body
    return get(run_id)


def _review_state(run_id):
    with connection() as db:
        row=db.execute('SELECT version,decision FROM runs WHERE id=?',(run_id,)).fetchone()
    return {'review_version':row['version'],'decision':row['decision']} if row else {'review_version':1,'decision':'待复核'}


def published_digest(run_id):
    with connection() as db:
        row=db.execute("SELECT value FROM meta WHERE key=?",('published:'+run_id,)).fetchone()
    return row['value'] if row else None


def record(state, digest, update=False, hook=None):
    """保存批次并登记封存版本，在一个事务内完成。

    只写批次行与封存版本，不动“最新批次”指针——那是发布成功才发生的
    事。这里失败整体回滚，该批次既有记录与封存版本不受影响；成功后
    即便发布失败，新记录也已留存为未发布版本。`hook` 仅供故障注入测试。
    """
    from datetime import datetime,timezone
    run_id=state['run_id'];db=connection()
    try:
        db.execute('BEGIN IMMEDIATE')
        clean={k:v for k,v in state.items() if k not in ('review_version','decision','seal','publish')}
        body=json.dumps(clean,ensure_ascii=False)
        if update:
            db.execute('INSERT INTO runs(id,body) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(run_id,body))
        else:
            db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,body))
        existing=db.execute('SELECT version FROM seals WHERE run_id=? AND digest=?',(run_id,digest)).fetchone()
        if existing:version=existing['version'];created=False
        else:
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 AS v FROM seals WHERE run_id=?',(run_id,)).fetchone()['v']
            db.execute('INSERT INTO seals(run_id,version,digest,body,created_at) VALUES (?,?,?,?,?)',
                (run_id,version,digest,body,datetime.now(timezone.utc).isoformat()))
            created=True
        if hook:hook(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {'run_id':run_id,'version':version,'digest':digest,'created':created}


def commit(state, digest, update=False, record_hook=None, publish_hook=None):
    """记录封存，再发布指针；供初始化等全有或全无的调用点使用。"""
    seal=record(state,digest,update=update,hook=record_hook)
    published=publish(state['run_id'],digest,hook=publish_hook)
    return {'seal':seal,'publish':published}


def publish(run_id, digest=None, hook=None):
    """发布一个已封存版本，并把“最新批次”指向它。

    指针写入与最新批次切换在同一事务；任何一步失败都回滚，旧发布指针
    与旧的最新批次都不变，已有封存版本也不会被删除或覆盖。
    `hook` 仅供故障注入测试。
    """
    db=connection()
    try:
        db.execute('BEGIN IMMEDIATE')
        if digest is None:
            row=db.execute('SELECT digest FROM seals WHERE run_id=? ORDER BY version DESC LIMIT 1',(run_id,)).fetchone()
        else:
            row=db.execute('SELECT digest FROM seals WHERE run_id=? AND digest=?',(run_id,digest)).fetchone()
        if not row:raise DomainError('PUBLISH','没有可发布的封存证据')
        new=row['digest'];old=db.execute("SELECT value FROM meta WHERE key=?",('published:'+run_id,)).fetchone()
        db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ('published:'+run_id,new))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
        if hook:hook(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {'run_id':run_id,'published':new,'previous':old['value'] if old else None,
        'changed':old is None or old['value']!=new}
