import io,csv,json,hashlib

VOLATILE_MANIFEST=('generated_at','elapsed_ms')

def canonical(state):
    """稳定证据视图：剔除每次运行都会变化的耗时与生成时刻。

    批次重跑只要输入、参数、规则与结论相同，摘要就相同，封存因此幂等；
    但 CSV 仍保留完整运行清单供人工核对。
    """
    manifest={k:v for k,v in (state.get('manifest') or {}).items() if k not in VOLATILE_MANIFEST}
    payload={
        'manifest':manifest,
        'reconcile':state.get('reconcile',{}),
        'rounding':state.get('rounding',{}),
        'incremental':state.get('incremental',{}),
        'calculations':state.get('calculations',[]),
        'quarantine':state.get('quarantine',[]),
    }
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def digest(state):
    """封存版本身份：对稳定证据取 SHA-256。"""
    return hashlib.sha256(canonical(state).encode('utf-8')).hexdigest()

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['口径',rid,'','',state.get('rounding',{}).get('policy',''),
        json.dumps(state.get('rounding',{}) or {'policy':'PUBLISH_OUTWARD_V1'},ensure_ascii=False,sort_keys=True)])
    if state.get('incremental'):
        writer.writerow(['补发',rid,'','','',json.dumps(state['incremental'],ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    return '\ufeff'+out.getvalue()

def checksum(state):
    """与封存身份一致的摘要，页面、导出与存储共用同一个值。"""
    return digest(state)
