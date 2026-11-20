import base64
import pytest
from app.workflow import load,run,supplement
from app.errors import DomainError
from app.modules.m5_calculate.increment import changed_hours,prepare


def _row(rid,bucket,kind,left,right,count,unit='人'):
    return f"{rid},{bucket},1,{kind},{left},{right},{count},{unit}"

HEADER="id,bucket,version,kind,left,right,count,unit"


def _csv(*rows):return "\n".join([HEADER,*rows,""]).encode()


def _full_hour(prefix,hour,zones,pairs):
    a,b,c=zones;ab,ac,bc=pairs
    bucket=f"2026-11-16T{hour:02d}:00:00+08:00"
    return [
        _row(f"{prefix}1",bucket,'zone','A','',a),
        _row(f"{prefix}2",bucket,'zone','B','',b),
        _row(f"{prefix}3",bucket,'zone','C','',c),
        _row(f"{prefix}4",bucket,'pair','A','B',ab),
        _row(f"{prefix}5",bucket,'pair','A','C',ac),
        _row(f"{prefix}6",bucket,'pair','B','C',bc),
    ]


def test_changed_hours_tracks_observation_differences():
    base=run(_csv(*_full_hour('1',9,(120,110,90),(40,30,20))),{})
    late=_csv(*_full_hour('2',12,(200,150,100),(60,40,30)))
    hours=changed_hours(base,prepare(_csv(*_full_hour('1',9,(120,110,90),(40,30,20)),*_full_hour('2',12,(200,150,100),(60,40,30))),{}))
    assert hours=={'2026-11-16T04:00:00+00:00'}


def test_supplement_only_recomputes_affected_hours():
    base=run(_csv(*_full_hour('1',9,(120,110,90),(40,30,20))),{})
    late=_csv(*_full_hour('2',12,(200,150,100),(60,40,30)))
    merged=supplement(base,late)
    assert merged['incremental']['affected']==['2026-11-16T04:00:00+00:00']
    assert merged['incremental']['recomputed']==['2026-11-16T04:00:00+00:00']
    # 原小时结论原样沿用，不因补发漂移
    assert merged['calculations'][0]==base['calculations'][0]
    assert len(merged['calculations'])==2


def test_supplement_keeps_untouched_hours_bit_identical():
    base=run(load('dirty.csv'),{})
    late=_csv(*_full_hour('2',12,(999,999,999),(900,900,900)))
    merged=supplement(base,late)
    before={c['id']:c for c in base['calculations']}
    for c in merged['calculations']:
        if c['id']!='2026-11-16T04:00:00+00:00':
            assert c==before[c['id']]


def test_supplement_isolated_bucket_stays_isolated():
    base=run(_csv(*_full_hour('1',9,(120,110,90),(40,30,20))),{})
    bad=_csv(
        _row('21','2026-11-16T12:00:00+08:00','zone','A','',100),
        _row('22','2026-11-16T12:00:00+08:00','zone','B','',100),
        _row('23','2026-11-16T12:00:00+08:00','zone','C','',100),
        _row('24','2026-11-16T12:00:00+08:00','pair','A','B',80),
        _row('25','2026-11-16T12:00:00+08:00','pair','A','C',80),
        _row('26','2026-11-16T12:00:00+08:00','pair','B','C',0),
    )
    merged=supplement(base,bad)
    assert all(c['id']!='2026-11-16T04:00:00+00:00' for c in merged['calculations'])
    assert any(q.get('bucket')=='2026-11-16T04:00:00+00:00' and q['code']=='CONSTRAINT' for q in merged['quarantine'])


def test_supplement_keeps_same_run_id():
    base=run(load('clean.csv'),{})
    late=_csv(*_full_hour('2',12,(999,999,999),(900,900,900)))
    merged=supplement(base,late)
    assert merged['run_id']==base['run_id']
    assert merged['manifest']['supplement_of']==base['run_id']


def test_supplement_is_deterministic():
    base=run(load('clean.csv'),{})
    late=_csv(*_full_hour('2',12,(200,150,100),(60,40,30)))
    a=supplement(base,late);b=supplement(base,late)
    assert a['archive']['sha256']==b['archive']['sha256']
    assert a['calculations']==b['calculations']


def test_incremental_matches_full_run_for_many_cases():
    """增量重算必须与"合并后全量重算"结论完全一致。"""
    import base64
    from app.modules.m5_calculate.increment import merge_source
    base=run(load('dirty.csv'),{})
    cases={
        'new_hour': _row('999','2026-11-16T22:00:00+08:00','zone','A','',200),
        'fraction': _row('999','2026-11-16T22:00:00+08:00','zone','A','','200.4'),
        'bad_num': _row('999','2026-11-16T22:00:00+08:00','zone','A','','N/A'),
        'bad_unit': _row('999','2026-11-16T22:00:00+08:00','zone','A','',200,'台'),
        'newer_existing': "00001,2026-11-16T09:00:00+08:00,2,zone,东门,,130,人",
        'cross_conflict': "00001,2026-11-16T22:00:00+08:00,1,zone,东门,,999,人",
    }
    for name,row in cases.items():
        content=_csv(row)
        merged,_=merge_source(base64.b64decode(base['source_b64']),content)
        full=run(merged,{});inc=supplement(base,content,{})
        assert full['calculations']==inc['calculations'],name
        assert full['business']==inc['business'],name
        assert sorted(map(str,full['quarantine']))==sorted(map(str,inc['quarantine'])),name
        assert full['reconcile']==inc['reconcile'],name


def test_supplement_conflict_recomputes_both_hours():
    base=run(_csv(*_full_hour('1',9,(120,110,90),(40,30,20))),{})
    # 复用原小时某个 id+version 但换到另一小时：原小时因此丢失该观测，
    # 两个小时都必须重新评估，否则原小时会留着过期结论。
    late=_csv(_row('11','2026-11-16T12:00:00+08:00','zone','A','',200))
    merged=supplement(base,late)
    assert set(merged['incremental']['affected'])=={'2026-11-16T01:00:00+00:00','2026-11-16T04:00:00+00:00'}
    assert merged['calculations']==[]  # 两小时都因同键冲突整组隔离
