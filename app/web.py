import base64
from flask import request,render_template,jsonify
from app.errors import DomainError
from app.config import TITLE,MODULES,SHORT
from app.workflow import run,load
from app import store

def payload():
    data=request.get_json(silent=True) or {}
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    if 'csv' in data and not isinstance(data['csv'],str):raise DomainError('CSV','CSV内容必须是文本')
    sample=data.get('sample',request.args.get('sample','dirty.csv'))
    content=data.get('csv')
    return (content.encode() if isinstance(content,str) else load(sample)),data.get('parameters',{})

def api(stage):
    content,parameters=payload();return jsonify(run(content,parameters,stage))

def page(stage,template):
    latest=store.history();state=store.view(latest[0]['id']) if latest else run(load('dirty.csv'),{})
    if stage>=5 and 'sample' not in request.args:
        # 估界、对账与归档页面直接展示已发布封存版本，与下载证据同一取舍。
        # 发布失败时指针仍指向旧版，页面随之停留在旧版，不展示未发布结果。
        data=state
        data.setdefault('manifest',state.get('manifest',{}))
    else:
        source=load(request.args['sample']) if 'sample' in request.args else base64.b64decode(state['source_b64']) if state.get('source_b64') else load('dirty.csv')
        data=run(source,state.get('manifest',{}).get('parameters',{}),stage)
    return render_template(template,title=TITLE,short=SHORT,modules=MODULES,current=stage,data=data,run=state,history=latest)