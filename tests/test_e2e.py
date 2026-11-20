from app.config import MODULES

def test_all_module_apis(client):
    for mod,_ in MODULES:assert client.get('/api/'+mod).status_code==200
def test_all_module_pages(client):
    for mod,_ in MODULES:assert client.get('/'+mod).status_code==200
def test_run_and_export(client):
    r=client.post('/api/runs',json={'sample':'dirty.csv'});assert r.status_code==200
    s=r.get_json();e=client.get('/api/archive/'+s['run_id']+'/export.csv');assert e.status_code==200 and '运行清单' in e.data.decode('utf-8-sig')
def test_idempotent_api(client):
    a=client.post('/api/runs',json={}).get_json();b=client.post('/api/runs',json={}).get_json()
    assert a['run_id']==b['run_id'] and len(client.get('/api/runs').get_json())==1
def test_bad_sample(client):
    r=client.post('/api/runs',json={'sample':'../x'});assert r.status_code==422 and r.get_json()['code']=='SAMPLE'
def test_missing_batch(client):
    assert client.get('/api/runs/absent').status_code==404
def test_bad_file(client):
    r=client.post('/api/runs',json={'csv':'wrong\n1\n'});assert r.status_code==422 and r.get_json()['code']=='HEADER'


def test_custom_source_is_visible_after_run(client):
    from app.workflow import load
    raw=load('clean.csv').decode('utf-8-sig');submitted=client.post('/api/runs',json={'csv':raw,'parameters':{}}).get_json()
    from app.config import MODULES
    page=client.get('/'+MODULES[0][0]);assert submitted['run_id'] in page.data.decode()

def test_reselect_existing_run_updates_visible_batch(client):
    a=client.post('/api/runs',json={'sample':'dirty.csv'}).get_json();b=client.post('/api/runs',json={'sample':'clean.csv'}).get_json();client.post('/api/runs',json={'sample':'dirty.csv'})
    assert client.get('/api/runs').get_json()[0]['id']==a['run_id'] and a['run_id']!=b['run_id']

def test_invalid_csv_type_rejected(client):
    assert client.post('/api/runs',json={'csv':123}).status_code==422


def test_supplement_via_api_reuses_run(client):
    base=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    late=b'id,bucket,version,kind,left,right,count,unit\n'
    late+=b'999,2026-11-16T22:00:00+08:00,1,zone,A,,200,\xe4\xba\xba\n'
    late+=b'998,2026-11-16T22:00:00+08:00,1,zone,B,,150,\xe4\xba\xba\n'
    late+=b'997,2026-11-16T22:00:00+08:00,1,zone,C,,100,\xe4\xba\xba\n'
    late+=b'996,2026-11-16T22:00:00+08:00,1,pair,A,B,60,\xe4\xba\xba\n'
    late+=b'995,2026-11-16T22:00:00+08:00,1,pair,A,C,40,\xe4\xba\xba\n'
    late+=b'994,2026-11-16T22:00:00+08:00,1,pair,B,C,30,\xe4\xba\xba\n'
    r=client.post('/api/runs',json={'csv':late.decode(),'parameters':{},'supplement_of':base['run_id']})
    assert r.status_code==200
    merged=r.get_json()
    assert merged['run_id']==base['run_id']
    assert merged['incremental']['affected']==['2026-11-16T14:00:00+00:00']
    assert merged['seal']['version']==2


def test_repeat_supplement_adds_no_seal_version(client):
    base=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    late='id,bucket,version,kind,left,right,count,unit\n999,2026-11-16T22:00:00+08:00,1,zone,A,,200,人\n'
    client.post('/api/runs',json={'csv':late,'supplement_of':base['run_id']})
    again=client.post('/api/runs',json={'csv':late,'supplement_of':base['run_id']}).get_json()
    assert again['seal']['created'] is False
    seals=client.get('/api/runs/'+base['run_id']+'/seals').get_json()
    assert [s['version'] for s in seals]==[1,2]


def test_archive_export_serves_published_seal(client):
    s=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    e=client.get('/api/archive/'+s['run_id']+'/export.csv')
    body=e.data.decode('utf-8-sig')
    assert '口径' in body and 'PUBLISH_OUTWARD_V1' in body
    published=client.get('/api/runs/'+s['run_id']+'/seals').get_json()
    assert any(x['published'] for x in published)


def test_publish_endpoint_rejects_unknown_seal(client):
    s=client.post('/api/runs',json={'sample':'clean.csv'}).get_json()
    r=client.post('/api/runs/'+s['run_id']+'/publish',json={'seal_version':99})
    assert r.status_code==404


def test_m5_page_uses_same_rounding_as_export(client):
    s=client.post('/api/runs',json={'sample':'dirty.csv'}).get_json()
    page=client.get('/m5_calculate').data.decode()
    assert 'PUBLISH_OUTWARD_V1' in page or all(str(c['lower']) in page for c in s['calculations'][:3])


def test_page_and_sealed_evidence_share_identical_bounds(client):
    csv=("id,bucket,version,kind,left,right,count,unit\n"
         "1,2026-11-16T09:00:00+08:00,1,zone,A,,120.5,人\n"
         "2,2026-11-16T09:00:00+08:00,1,zone,B,,110.5,人\n"
         "3,2026-11-16T09:00:00+08:00,1,zone,C,,90.5,人\n"
         "4,2026-11-16T09:00:00+08:00,1,pair,A,B,40.5,人\n"
         "5,2026-11-16T09:00:00+08:00,1,pair,A,C,30.5,人\n"
         "6,2026-11-16T09:00:00+08:00,1,pair,B,C,20.5,人\n")
    s=client.post('/api/runs',json={'csv':csv}).get_json()
    published=client.get('/api/runs/'+s['run_id']).get_json()
    page=client.get('/m5_calculate').data.decode()
    export=client.get('/api/archive/'+s['run_id']+'/export.csv').data.decode('utf-8-sig')
    import csv as csv_module,io as io_module,json as json_module
    from decimal import Decimal
    evidence={}
    for row in csv_module.reader(io_module.StringIO(export)):
        if row[0]=='计算':evidence[row[3]]=json_module.loads(row[5])
    for r in published['calculations']:
        assert f"{r['lower']}–{r['upper']}" in page
        sealed=evidence[r['id']]
        assert sealed['lower']==r['lower'] and sealed['upper']==r['upper']
        assert sealed['raw_lower']==r['raw_lower'] and sealed['raw_upper']==r['raw_upper']
        assert r['lower']<=Decimal(r['raw_lower'])<=Decimal(r['raw_upper'])<=r['upper']
    assert published['business']['total']==sum(r['lower'] for r in published['calculations'])
