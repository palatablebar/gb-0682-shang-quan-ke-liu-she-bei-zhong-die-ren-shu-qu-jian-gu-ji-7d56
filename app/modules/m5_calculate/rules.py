from decimal import Decimal
from app.errors import DomainError
from app.rounding import exact, publish_interval, decimal_text, DESCRIPTION, POLICY

def bounds(model):
    zones=model['zones'];keys=sorted(zones);pairs=model['pairs'];values=[exact(zones[k]) for k in keys]
    if len(keys)==1:return values[0],values[0],Decimal(0),Decimal(0)
    if len(keys)==2:
        common=pairs.get('|'.join(keys))
        if common is not None:
            total=sum(values)-exact(common);return total,total,exact(common),exact(common)
        return max(values),sum(values),Decimal(0),min(values)
    a,b,c=keys;ab=exact(pairs['|'.join([a,b])]);ac=exact(pairs['|'.join([a,c])]);bc=exact(pairs['|'.join([b,c])])
    low=max(Decimal(0),ab+ac-values[0],ab+bc-values[1],ac+bc-values[2]);high=min(ab,ac,bc)
    if model.get('triple') is not None:
        triple=exact(model['triple']);low=max(low,triple);high=min(high,triple)
    if low>high:raise DomainError('CONSTRAINT','三方观测不存在可行人数')
    base=sum(values)-ab-ac-bc
    return base+low,base+high,low,high


def calculation(item):
    """单个小时桶的估界：观测层精确值 -> 公布层向外取整。"""
    raw_low,raw_high,raw_tlo,raw_thi=bounds(item['model'])
    low,high=publish_interval(raw_low,raw_high)
    tlo,thi=publish_interval(raw_tlo,raw_thi)
    rounded=Decimal(low)!=raw_low or Decimal(high)!=raw_high
    triple_rounded=Decimal(tlo)!=raw_tlo or Decimal(thi)!=raw_thi
    return {'id':item['bucket'],'line':item['line'],'lower':low,'upper':high,'width':high-low,
        'raw_lower':decimal_text(raw_low),'raw_upper':decimal_text(raw_high),
        'triple_lower':tlo,'triple_upper':thi,
        'raw_triple_lower':decimal_text(raw_tlo),'raw_triple_upper':decimal_text(raw_thi),
        'rounded':'FLOOR_CEIL' if rounded else 'NONE',
        'triple_rounded':'FLOOR_CEIL' if triple_rounded else 'NONE',
        'code':'EXACT' if raw_low==raw_high else 'INTERVAL'}


def aggregate(results):
    results=sorted(results,key=lambda r:r['id'])
    return {'total':sum(r['lower'] for r in results),'upper_total':sum(r['upper'] for r in results),
        'raw_total':decimal_text(sum(exact(r['raw_lower']) for r in results)),
        'raw_upper_total':decimal_text(sum(exact(r['raw_upper']) for r in results)),
        'buckets':len(results),'unit':'客次','rounding':POLICY,'rounding_note':DESCRIPTION}


def apply(state):
    results=[];bad=set()
    for item in state['models']:
        try:
            results.append(calculation(item))
        except DomainError as e:
            bad.add(item['bucket'])
            for r in state['rows']:
                if r['bucket']==item['bucket']:state['quarantine'].append({'source':r.get('_source','base'),'stage':5,'bucket':item['bucket'],'line':r['_line'],'id':r['id'],'code':e.code,'raw':r['_raw']})
    state['rows']=[r for r in state['rows'] if r['bucket'] not in bad]
    state['calculations']=sorted(results,key=lambda r:r['id'])
    state['business']=aggregate(results)
    state['rounding']={'policy':POLICY,'description':DESCRIPTION}
    return state
