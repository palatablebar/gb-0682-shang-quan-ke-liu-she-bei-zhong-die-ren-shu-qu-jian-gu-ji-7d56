from app.rounding import exact, decimal_text, POLICY

def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine'])
    accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    # 日汇必须能由逐桶公布值精确相加得到；原始估计另存一份供审计回看。
    rows=state.get('calculations',[])
    total=sum(r['lower'] for r in rows);upper=sum(r['upper'] for r in rows)
    raw_total=decimal_text(sum(exact(r['raw_lower']) for r in rows)) if rows else '0'
    raw_upper=decimal_text(sum(exact(r['raw_upper']) for r in rows)) if rows else '0'
    business=dict(state.get('business',{}))
    business.update(total=total,upper_total=upper,raw_total=raw_total,raw_upper_total=raw_upper,
        buckets=len(rows),unit=business.get('unit','客次'),rounding=POLICY)
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'accounted':accounted,'balanced':accounted==state['input_count'],
        'business':business,
        'reconcile_check':{'bucket_lower_sum':total,'bucket_upper_sum':upper,
            'day_lower':business['total'],'day_upper':business['upper_total'],
            'consistent':total==business['total'] and upper==business['upper_total']},
        'rounding':state.get('rounding',{'policy':POLICY})}
