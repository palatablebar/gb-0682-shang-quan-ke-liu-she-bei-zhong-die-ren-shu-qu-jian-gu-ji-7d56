import pytest
from decimal import Decimal
from app.workflow import load,run
from app.errors import DomainError
from app.modules.m5_calculate.rules import bounds,apply
from app.rounding import publish_interval,publish_lower,publish_upper

def test_two_zone_union():assert bounds({'zones':{'A':100,'B':80},'pairs':{'A|B':30},'triple':None})[:2]==(150,150)
def test_two_unknown_overlap():assert bounds({'zones':{'A':100,'B':80},'pairs':{},'triple':None})[:2]==(100,180)
def test_three_way_feasible():assert bounds({'zones':{'A':120,'B':110,'C':90},'pairs':{'A|B':40,'A|C':30,'B|C':20},'triple':None})[:2]==(230,250)
def test_three_way_contradiction():
    # TRAP-19
    with pytest.raises(DomainError) as e:bounds({'zones':{'A':100,'B':100,'C':100},'pairs':{'A|B':80,'A|C':80,'B|C':0},'triple':None})
    assert e.value.code=='CONSTRAINT'
def test_real_first_interval(raw):
    r=run(raw,{})['calculations'][0];assert r['lower']==230 and r['upper']==250 and r['code']=='INTERVAL'
def test_real_interval_totals(raw):
    s=run(raw,{});assert s['business']['total']==sum(r['lower'] for r in s['calculations']) and s['business']['upper_total']>s['business']['total']
def test_real_bad_bucket_isolated(raw):
    s=run(raw,{},4);s['models'][0]['model']['pairs']={'A|B':110,'A|C':90,'B|C':0};r=apply(s);assert len(r['calculations'])==11 and any(q['code']=='CONSTRAINT' for q in r['quarantine'])

# ---- 取整口径裁决 ----
def test_observation_layer_keeps_fraction():
    assert bounds({'zones':{'A':Decimal('100.4'),'B':Decimal('80.2')},'pairs':{'A|B':Decimal('30.6')},'triple':None})[:2]==(Decimal('150.0'),)*2

def test_publish_rounds_outward():
    assert publish_lower(Decimal('149.6'))==149 and publish_upper(Decimal('150.4'))==151
    assert publish_lower(Decimal('150'))==150 and publish_upper(Decimal('150'))==150

def test_publish_interval_never_empty_for_fractional_bucket():
    # 小数客流区间宽度可以小于 1；向外取整后仍是非空整数区间，不会被隔离。
    low,high=publish_interval(Decimal('10.2'),Decimal('10.7'));assert (low,high)==(10,11)

def test_fractional_observation_publishes_integer_bounds():
    rows="\n".join([
        "id,bucket,version,kind,left,right,count,unit",
        "1,2026-11-16T09:00:00+08:00,1,zone,A,,100.4,人",
        "2,2026-11-16T09:00:00+08:00,1,zone,B,,80.2,人",
        "3,2026-11-16T09:00:00+08:00,1,pair,A,B,30.6,人",""])
    s=run(rows.encode(),{})
    r=s['calculations'][0]
    assert r['lower']==150 and r['upper']==150 and r['code']=='EXACT'
    assert r['raw_lower']=='150' and r['raw_upper']=='150'
    assert isinstance(r['lower'],int) and isinstance(r['upper'],int)

def test_fractional_triple_is_not_isolated_by_rounding():
    rows="\n".join([
        "id,bucket,version,kind,left,right,count,unit",
        "1,2026-11-16T09:00:00+08:00,1,zone,A,,120.5,人",
        "2,2026-11-16T09:00:00+08:00,1,zone,B,,110.5,人",
        "3,2026-11-16T09:00:00+08:00,1,zone,C,,90.5,人",
        "4,2026-11-16T09:00:00+08:00,1,pair,A,B,40.5,人",
        "5,2026-11-16T09:00:00+08:00,1,pair,A,C,30.5,人",
        "6,2026-11-16T09:00:00+08:00,1,pair,B,C,20.5,人",""])
    s=run(rows.encode(),{})
    r=s['calculations'][0]
    assert not any(q['code']=='CONSTRAINT' for q in s['quarantine'])
    assert r['lower']==230 and r['upper']==251

def test_observation_and_publish_use_same_policy(raw):
    s=run(raw,{})
    assert s['rounding']['policy']=='PUBLISH_OUTWARD_V1'
    assert s['reconcile']['rounding']['policy']==s['rounding']['policy']
    for r in s['calculations']:
        assert r['lower']==int(Decimal(r['raw_lower']).to_integral_value(rounding='ROUND_FLOOR'))
        assert r['upper']==int(Decimal(r['raw_upper']).to_integral_value(rounding='ROUND_CEILING'))
    assert s['business']['total']==sum(r['lower'] for r in s['calculations'])

def test_small_fractional_footfall_not_isolated():
    rows="\n".join([
        "id,bucket,version,kind,left,right,count,unit",
        "1,2026-11-16T09:00:00+08:00,1,zone,A,,0.2,人",
        "2,2026-11-16T09:00:00+08:00,1,zone,B,,0.3,人",""])
    s=run(rows.encode(),{})
    assert s['reconcile']['isolated']==0
    assert s['calculations'][0]['lower']==0 and s['calculations'][0]['upper']==1
