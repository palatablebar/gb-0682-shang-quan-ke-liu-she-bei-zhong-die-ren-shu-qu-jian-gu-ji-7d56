"""补发增量重算：只重做补发行覆盖的小时桶。

补发只是同一批次的证据修订：批次号不变，封存版本按证据摘要递增。

- 合并后的输入完整走采信、归整与分桶，这些步骤只做元数据与版本选择。
- 重叠约束与估界只对补发行落到的 UTC 小时桶重算；其余小时桶沿用
  原批次已发布的结论，不因补发而漂移。
- 同一批重跑得到同一证据摘要，封存层据此去重，不新增版本。
"""
import base64,csv,io,json
from collections import defaultdict

from app.errors import DomainError
from app.manifest import manifest
from app.modules.m1_ingest import service as ingest
from app.modules.m1_ingest.decode import decode
from app.modules.m2_mapping import service as mapping
from app.modules.m3_versions import rules as versions
from app.modules.m4_segments import rules as segments
from app.modules.m5_calculate import rules as calculate


def prepare(raw, parameters):
    state=ingest.execute(raw,parameters)
    state=mapping.execute(state)
    return versions.apply(state)


def _logical_rows(raw):
    """CSV 逻辑记录（含引号内换行），返回表头与数据行。"""
    text,_=decode(raw)
    rows=list(csv.reader(io.StringIO(text,newline='')))
    if not rows:return [], []
    header=rows[0]
    return header,[r for r in rows[1:] if r and any(s.strip() for s in r) and r!=header]


def merge_source(base_source, content):
    """把补发数据行追加到原输入，原批次字节保持不变。

    - 补发行按列名对齐到原表头，缺列补空，避免因列数不同被 WIDTH 隔离。
    - 原批次原样保留，原有行号、空行与重复表头都不受影响；补发沿用
      原批次编码（GB18030 与 UTF-8 都不需要转码）。
    - 已存在的逻辑行不再追加；同一批补发重跑因此合并不出新行，
      封存摘要不变，不会新增封存版本。
    """
    base_header,base_rows=_logical_rows(base_source)
    new_header,new_rows=_logical_rows(content)
    if new_rows and not set(new_header)<=set(base_header):
        raise DomainError('HEADER','补发包含原批次没有的列')
    seen={tuple(r) for r in base_rows}
    fresh=[]
    for row in new_rows:
        item=dict(zip(new_header,row))
        aligned=[item.get(name,'') for name in base_header]
        if tuple(aligned) in seen:continue
        seen.add(tuple(aligned));fresh.append(aligned)
    if not fresh:return base_source,[]
    _,encoding=decode(base_source)
    # BOM 只属于文件开头；追加段按裸 UTF-8 写，避免把 BOM 塞进数据行。
    target='utf-8' if encoding=='utf-8-sig' else encoding
    out=io.StringIO(newline='');writer=csv.writer(out)
    for row in fresh:writer.writerow(row)
    appended=out.getvalue().encode(target)
    if not base_source.endswith((b'\n',b'\r')):base_source+=b'\r\n'
    return base_source+appended,fresh


def _signature(row):
    return (row.get('kind'),row.get('left'),row.get('right',''),str(row.get('count')))


def _by_bucket(observations):
    grouped=defaultdict(list)
    for row in observations:grouped[row['bucket']].append(_signature(row))
    return {bucket:tuple(sorted(rows)) for bucket,rows in grouped.items()}


def changed_hours(base, prepared):
    """原批次与合并后批次之间观测集合发生变化的小时桶。

    这覆盖两类影响：补发行直接落到的桶，以及因 (id,version) 同键冲突
    被连带隔离、从而丢失观测的其它桶。未变化的桶结论必然相同，可以沿用。
    """
    before=_by_bucket(base.get('observations',[]));after=_by_bucket(prepared['observations'])
    return {bucket for bucket in set(before)|set(after) if before.get(bucket)!=after.get(bucket)}


def _raw_affected(content):
    """从补发明细本身取受影响小时，即使该行随后被隔离也要重算所在小时。"""
    from app.modules.m2_mapping.coerce import instant
    from datetime import datetime
    header,rows=_logical_rows(content)
    if not rows or 'bucket' not in header:return set()
    index=header.index('bucket');hours=set()
    for row in rows:
        try:
            moment=datetime.fromisoformat(instant(row[index]))
            hours.add(moment.replace(minute=0,second=0,microsecond=0).isoformat())
        except Exception:continue
    return hours


def supplement(base, content, parameters=None):
    if len(content)>2_000_000:raise DomainError('SIZE','补发文件超过2MB',413)
    provided=dict(parameters) if parameters else {}
    base_params=dict(base.get('manifest',{}).get('parameters',{}) or {})
    # 未显式传参时沿用原批次参数；显式传参则以本次为准。
    params=provided or base_params
    if not isinstance(params,dict):raise DomainError('PARAMETERS','参数必须是对象')
    base_source=base64.b64decode(base['source_b64'])
    merged,fresh_rows=merge_source(base_source,content)
    if len(merged)>2_000_000:raise DomainError('SIZE','合并后的输入超过2MB',413)
    if not fresh_rows:
        # 同一批补发重跑：不新增任何证据，直接复用原批次结论。
        return base

    prepared=prepare(merged,params)
    affected=changed_hours(base,prepared)|_raw_affected(content)
    # 采信与归整的隔离在合并输入上重算；观测阶段的隔离沿用或重算。
    prepared_early=[q for q in prepared['quarantine'] if q.get('stage',0)<4 or not q.get('bucket')]

    base_calcs={c['id']:c for c in base.get('calculations',[])}
    base_isolated=defaultdict(list)
    for q in base.get('quarantine',[]):
        if q.get('stage',0)>=4 and q.get('bucket'):base_isolated[q['bucket']].append(q)
    # 采信与归整阶段的隔离在合并输入上会重新得到，无需另行沿用。

    observations=defaultdict(list)
    for row in prepared['observations']:observations[row['bucket']].append(row)
    rows_by_bucket=defaultdict(list)
    for row in prepared['rows']:rows_by_bucket[row['bucket']].append(row)

    results=[];isolated=[];recomputed=[];reused=[]
    for bucket,rows in sorted(observations.items()):
        if bucket not in affected:
            if bucket in base_calcs:
                results.append(base_calcs[bucket]);reused.append(bucket)
            elif bucket in base_isolated:
                isolated.extend(base_isolated[bucket])
            continue
        recomputed.append(bucket)
        try:
            item={'bucket':bucket,'model':segments.model(rows),'line':min(r['_line'] for r in rows)}
            results.append(calculate.calculation(item))
        except DomainError as error:
            isolated.extend({'source':r.get('_source','base'),'stage':4,'bucket':bucket,
                'line':r['_line'],'id':r['id'],'code':error.code,'raw':r['_raw']} for r in rows_by_bucket[bucket])

    # 补发未触及、但原批次仍有的小时桶，原样保留（含原批次隔离结论）。
    for bucket,item in base_calcs.items():
        if bucket not in observations and bucket not in affected:
            results.append(item);reused.append(bucket)
    for bucket,items in base_isolated.items():
        if bucket not in observations and bucket not in affected:
            isolated.extend(items)

    state=dict(prepared)
    accepted={r['id'] for r in results}
    state['rows']=[r for r in prepared['rows'] if r['bucket'] in accepted]
    state['calculations']=sorted(results,key=lambda r:r['id'])
    state['business']=calculate.aggregate(results)
    state['rounding']={'policy':calculate.POLICY,'description':calculate.DESCRIPTION}
    state['quarantine']=sorted(prepared_early+isolated,key=lambda q:(q.get('line',0),q.get('code','')))
    state['incremental']={'base_run':base['run_id'],'affected':sorted(affected),
        'recomputed':sorted(recomputed),'reused':sorted(reused),
        'policy':'影响小时桶重算，其余沿用'}

    state=json.loads(json.dumps(state,default=str,ensure_ascii=False))
    # 补发不改变批次号；证据修订由封存版本表达。
    state['run_id']=base['run_id']
    state['manifest']=manifest(merged,params,0)
    state['manifest']['supplement_of']=base['run_id']
    state['manifest']['supplement_sha256']=base['manifest'].get('sha256') if base.get('manifest') else None
    state['source_b64']=base64.b64encode(merged).decode('ascii')
    from app.modules.m6_reconcile.compare import balance
    state['reconcile']=balance(state)
    from app.modules.m7_archive.export_out import checksum
    state['archive']={'evidence_count':len(state['calculations'])+len(state['quarantine']),
        'ready':state['reconcile']['balanced'],'sha256':checksum(state)}
    return state
