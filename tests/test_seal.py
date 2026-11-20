import pytest
from app import store
from app.errors import DomainError
from app.modules.m7_archive.export_out import digest,checksum,bundle
from app.workflow import load,run,supplement
from app.modules.m5_calculate.increment import changed_hours


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'seal.db'));return tmp_path


def _seal(state,version=None):
    return store.seal(state['run_id'],state,digest(state))


def test_same_run_twice_adds_no_version(db):
    state=run(load('clean.csv'),{})
    first=_seal(state);second=_seal(state)
    assert first['version']==1 and second['version']==1
    assert second['created'] is False and len(store.seals(state['run_id']))==1


def test_rerun_with_same_evidence_does_not_add_version(db):
    # 同一批重跑：耗时与生成时刻不同，但证据摘要相同，封存版本不增加。
    a=run(load('clean.csv'),{});b=run(load('clean.csv'),{})
    assert digest(a)==digest(b)
    _seal(a);_seal(b)
    assert len(store.seals(a['run_id']))==1


def test_changed_evidence_adds_one_version(db):
    base=run(_hour_csv(9,120),{});late=_hour_csv(12,200)
    _seal(base)
    merged=supplement(base,late)
    _seal(merged)
    versions=[s['version'] for s in store.seals(base['run_id'])]
    assert versions==[1,2]


def test_publish_is_idempotent(db):
    state=run(load('clean.csv'),{});seal=_seal(state)
    first=store.publish(state['run_id'],seal['digest'])
    second=store.publish(state['run_id'],seal['digest'])
    assert first['changed'] is True and second['changed'] is False
    assert store.published_digest(state['run_id'])==seal['digest']


def test_publish_failure_keeps_previous_version(db):
    state=run(load('clean.csv'),{});_seal(state)
    store.publish(state['run_id'])
    old=store.published_digest(state['run_id'])

    def explode(conn):raise RuntimeError('disk full')

    with pytest.raises(RuntimeError):store.publish(state['run_id'],old,hook=explode)
    assert store.published_digest(state['run_id'])==old
    assert len(store.seals(state['run_id']))==1


def test_failed_publish_does_not_lose_seal_records(db):
    state=run(load('clean.csv'),{});seal=_seal(state)
    def explode(conn):raise RuntimeError('network')
    with pytest.raises(RuntimeError):store.publish(state['run_id'],seal['digest'],hook=explode)
    assert store.sealed(state['run_id'])['seal_version']==1


def test_publish_requires_existing_seal(db):
    state=run(load('clean.csv'),{})
    with pytest.raises(DomainError) as e:store.publish(state['run_id'],'0'*64)
    assert e.value.code=='PUBLISH'


def test_export_uses_published_seal(db):
    state=run(load('clean.csv'),{});seal=_seal(state);store.publish(state['run_id'],seal['digest'])
    exported=store.sealed(state['run_id'])
    assert exported['archive']['sha256']==seal['digest']
    assert exported['seal_version']==1


def test_export_rounding_matches_page_rounding(db):
    state=run(load('dirty.csv'),{});seal=_seal(state);store.publish(state['run_id'],seal['digest'])
    exported=store.sealed(state['run_id'])
    for r in exported['calculations']:
        assert r['lower']==int(r['lower']) and r['upper']==int(r['upper'])
    assert exported['reconcile']['rounding']['policy']=='PUBLISH_OUTWARD_V1'
    assert '口径' in bundle(exported)


def test_checksum_matches_archive(db):
    state=run(load('clean.csv'),{})
    assert checksum(state)==state['archive']['sha256']


def _hour_csv(hour,count):
    bucket=f"2026-11-16T{hour:02d}:00:00+08:00"
    rows=["id,bucket,version,kind,left,right,count,unit",
        f"1,{bucket},1,zone,A,,{count},人",
        f"2,{bucket},1,zone,B,,{count-10},人",
        f"3,{bucket},1,pair,A,B,{count-60},人",""]
    return "\n".join(rows).encode()


def test_record_failure_leaves_nothing(db,monkeypatch):
    state=run(load('clean.csv'),{})
    def explode(conn):raise RuntimeError('io error')
    with pytest.raises(RuntimeError):store.record(state,digest(state),hook=explode)
    assert store.history()==[]
    assert store.seals(state['run_id'])==[]


def test_publish_failure_after_record_keeps_new_record_and_old_pointer(db):
    first=run(load('clean.csv'),{})
    store.record(first,digest(first));store.publish(first['run_id'],digest(first))
    old=store.published_digest(first['run_id'])

    second=run(_hour_csv(9,300),{})
    seal=store.record(second,digest(second))
    def explode(conn):raise RuntimeError('pointer write failed')
    with pytest.raises(RuntimeError):store.publish(second['run_id'],seal['digest'],hook=explode)

    # 新封存记录已留存，旧发布指针与对外版本不变
    assert seal['created'] is True
    assert len(store.seals(second['run_id']))==1
    assert store.published_digest(first['run_id'])==old
    if first['run_id']==second['run_id']:
        assert store.sealed(first['run_id'])['seal_digest']==old


def test_api_publish_failure_returns_503_and_keeps_old(client,monkeypatch):
    base=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    old=client.get('/api/runs/'+base['run_id']+'/seals').get_json()[0]['digest']
    from app import store as store_module
    real=store_module.publish
    def boom(run_id,digest=None,hook=None):raise RuntimeError('down')
    monkeypatch.setattr(store_module,'publish',boom)
    late='id,bucket,version,kind,left,right,count,unit\n999,2026-11-16T22:00:00+08:00,1,zone,A,,200,人\n'
    r=client.post('/api/runs',json={'csv':late,'supplement_of':base['run_id']})
    assert r.status_code==503 and r.get_json()['code']=='PUBLISH'
    monkeypatch.setattr(store_module,'publish',real)
    seals=client.get('/api/runs/'+base['run_id']+'/seals').get_json()
    assert [s['version'] for s in seals]==[1,2]
    published=[s for s in seals if s['published']]
    assert len(published)==1 and published[0]['digest']==old
    # 页面与导出仍指向旧发布版本
    assert client.get('/api/archive/'+base['run_id']+'/export.csv').status_code==200


def test_failed_publish_does_not_advance_latest_batch(client,monkeypatch):
    base=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    latest_before=client.get('/api/runs').get_json()[0]['id']
    from app import store as store_module
    real=store_module.publish
    def boom(run_id,digest=None,hook=None):raise RuntimeError('down')
    monkeypatch.setattr(store_module,'publish',boom)
    late='id,bucket,version,kind,left,right,count,unit\n999,2026-11-16T22:00:00+08:00,1,zone,A,,200,人\n'
    assert client.post('/api/runs',json={'csv':late,'supplement_of':base['run_id']}).status_code==503
    monkeypatch.setattr(store_module,'publish',real)
    assert client.get('/api/runs').get_json()[0]['id']==latest_before
    # 新封存版本仍可稍后重发
    out=client.post('/api/runs/'+base['run_id']+'/publish',json={'seal_version':2}).get_json()
    assert out['changed'] is True
    assert [s['version'] for s in client.get('/api/runs/'+base['run_id']+'/seals').get_json() if s['published']]==[2]


def test_view_prefers_published_when_new_seal_unpublished(client,monkeypatch):
    base=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    published_calcs=len(base['calculations'])
    from app import store as store_module
    real=store_module.publish
    def boom(run_id,digest=None,hook=None):raise RuntimeError('down')
    monkeypatch.setattr(store_module,'publish',boom)
    late='id,bucket,version,kind,left,right,count,unit\n999,2026-11-16T22:00:00+08:00,1,zone,A,,200,人\n'
    assert client.post('/api/runs',json={'csv':late,'supplement_of':base['run_id']}).status_code==503
    monkeypatch.setattr(store_module,'publish',real)
    view=client.get('/api/runs/'+base['run_id']).get_json()
    assert len(view['calculations'])==published_calcs and 'incremental' not in view
    candidate=client.get('/api/runs/'+base['run_id']+'/seals/2').get_json()
    assert candidate['incremental']['affected']==['2026-11-16T14:00:00+00:00']
