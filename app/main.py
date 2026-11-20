import importlib
from flask import Flask,jsonify,request,redirect,Response
from app.config import MODULES
from app.errors import DomainError
from app.workflow import run,load,supplement
from app import store
from app.web import payload
from app.modules.m7_archive.export_out import bundle

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload()
        data=request.get_json(silent=True) or {}
        base_id=data.get('supplement_of')
        # 补发只重做受影响的小时桶；普通提交走全量重算。
        state=supplement(store.get(base_id),content,params) if base_id else run(content,params)
        from app.modules.m7_archive.export_out import digest
        # 先落记录与封存，再切发布指针。批次行始终反映最近一次记录的
        # 证据；历史封存版本只增不改。记录失败整体回滚；发布失败时新封存
        # 版本已留存，旧发布版本继续对外，不丢失任何记录。
        seal=store.record(state,digest(state),update=True)
        try:published=store.publish(state['run_id'],seal['digest'])
        except Exception:
            raise DomainError('PUBLISH',f"批次 {state['run_id']} 已记录为封存 V{seal['version']}，发布失败，对外仍为旧版",503)
        return jsonify(dict(store.view(state['run_id']),seal=seal,publish=published))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.view(run_id))
    @app.get('/api/runs/<run_id>/seals')
    def list_seals(run_id):return jsonify(store.seals(run_id))
    @app.get('/api/runs/<run_id>/seals/<int:version>')
    def get_seal(run_id,version):return jsonify(store.sealed(run_id,version))
    @app.post('/api/runs/<run_id>/publish')
    def publish(run_id):
        data=request.get_json(silent=True) or {}
        if data.get('seal_version') is not None:
            seal=store.sealed(run_id,int(data['seal_version']));digest=seal['seal_digest']
        else:digest=None
        return jsonify(store.publish(run_id,digest))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        # 有封存证据时导出已发布版本，页面与封存证据共用同一取舍。
        try:state=store.sealed(run_id)
        except DomainError:state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{})
            from app.modules.m7_archive.export_out import digest
            store.commit(state,digest(state))
    return app

app=create_app()
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
