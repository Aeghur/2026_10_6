"""Local gated-ramp training dashboard. Starts IDLE; no hardware access."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','4')
import argparse
import base64
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import signal
import threading
import time
import numpy as np
import torch
from gated_frequency import VERSION, Config, Ranker, experiment, baseline

RUNTIME={'epochs','batch_size','learning_rate','max_minutes'}


def conditions(c):
    return {k:v for k,v in asdict(c).items() if k not in RUNTIME}


def atomic_save(data,path):
    temp=path.with_suffix('.tmp');torch.save(data,temp);os.replace(temp,path)


def png(image):
    stream=io.BytesIO();image.save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


class Cancelled(Exception): pass


class Session:
    def __init__(self,directory,config):
        self.directory=Path(directory).resolve();self.config=config.validate()
        self.worker=None;self.event=threading.Event();self.lock=threading.RLock()
        self.model=None;self.optimizer=None;self.epoch=0;self.cursor=0;self.best=-1.
        self.state={'state':'idle','message':'就绪；尚未开始训练','history':[],'preview':[], 'epoch':0,'cursor':0}
        latest=self.directory/'latest.pt'
        if latest.exists():
            cp=torch.load(latest,map_location='cpu',weights_only=True)
            if cp.get('version')!=VERSION:raise ValueError('Checkpoint version differs')
            saved=Config(**cp['config']).validate()
            for key in RUNTIME:setattr(saved,key,getattr(config,key))
            self.config=saved
            self.epoch,self.cursor,self.best=cp['epoch'],cp['cursor'],cp['best']
            self.state.update(epoch=cp['epoch'],cursor=cp['cursor'],message='已找到进度；点击继续训练')
            metrics=self.directory/'metrics.jsonl'
            if metrics.exists():
                rows=[]
                for line in metrics.read_text(encoding='utf-8').splitlines():
                    try:rows.append(json.loads(line))
                    except json.JSONDecodeError:continue
                self.state['history']=rows[-150:]
                for row in rows:
                    if row.get('stage')=='validation':self.state['validation']=row
                    if row.get('stage')=='test':self.state['test']=row

    def public(self):
        with self.lock:
            return json.loads(json.dumps(dict(self.state,config=asdict(self.config),run=str(self.directory),running=bool(self.worker and self.worker.is_alive()))))

    def update(self,**values):
        with self.lock:self.state.update(values)

    def check(self):
        if self.event.is_set():raise Cancelled()

    def start(self,config,action):
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('已有任务运行；请先停止')
            config.validate()
            if (self.directory/'latest.pt').exists() and conditions(config)!=conditions(self.config):
                raise ValueError('采集/数据条件已锁定；更换条件请使用新的 --run 目录')
            self.config=config;self.event.clear()
            self.update(state='working',message='准备 '+action,error=None)
            self.worker=threading.Thread(target=self.work,args=(action,),daemon=False);self.worker.start()

    def stop(self):self.event.set()

    def load(self,best=False):
        path=self.directory/('best.pt' if best else 'latest.pt')
        torch.manual_seed(self.config.seed)
        model=Ranker();opt=torch.optim.Adam(model.parameters(),lr=self.config.learning_rate)
        if path.exists():
            cp=torch.load(path,map_location='cpu',weights_only=True)
            if cp['version']!=VERSION or conditions(Config(**cp['config']))!=conditions(self.config):
                raise ValueError('Saved run conditions differ; choose a new --run')
            model.load_state_dict(cp['model']);opt.load_state_dict(cp['optimizer'])
            if not best:self.epoch,self.cursor,self.best=cp['epoch'],cp['cursor'],cp['best']
        elif best:raise ValueError('先完成至少一轮训练，生成 best.pt 后再进行独立测试')
        for group in opt.param_groups:group['lr']=self.config.learning_rate
        return model,opt

    def save(self,path='latest.pt'):
        self.directory.mkdir(parents=True,exist_ok=True)
        atomic_save({'version':VERSION,'config':asdict(self.config),'model':self.model.state_dict(),
                     'optimizer':self.optimizer.state_dict(),'epoch':self.epoch,'cursor':self.cursor,'best':self.best},self.directory/path)

    def record(self,row):
        self.directory.mkdir(parents=True,exist_ok=True)
        with (self.directory/'metrics.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        with self.lock:
            self.state['history']=(self.state['history']+[row])[-150:]

    def dataset(self,split):
        c=self.config;n=getattr(c,split+'_cases')
        key=hashlib.sha256(json.dumps([VERSION,conditions(c),split],sort_keys=True).encode()).hexdigest()[:16]
        path=self.directory/('data_'+split+'_'+key+'.npz')
        if path.exists():
            with np.load(path,allow_pickle=False) as a:return a['features'],a['labels']
        xs=[];ys=[];split_id={'train':11,'validation':22,'test':33}[split]
        rng=np.random.default_rng([c.seed,split_id]);labels=[]
        while len(labels)<n:labels.extend(rng.permutation(len(c.grid)).tolist())
        for i,label in enumerate(labels[:n]):
            self.check()
            seed=np.random.SeedSequence([c.seed,split_id,i]).generate_state(1)[0]
            x,meta,ims=experiment(c,int(c.grid[label]),int(seed),self.check,preview=i%40==0)
            xs.append(x);ys.append(label)
            if i%8==0 or i==n-1:
                self.update(message=f'生成 {split} 有限像素观测 {i+1}/{n}',progress=(i+1)/n)
            if ims:self.show(ims,meta,int(c.grid[label]),x)
        self.directory.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        with temp.open('wb') as f:np.savez_compressed(f,features=np.asarray(xs),labels=np.asarray(ys))
        os.replace(temp,path)
        return np.asarray(xs),np.asarray(ys)

    def show(self,ims,meta,truth,features):
        self.update(preview=[{'image':png(im),'active_us':s.active_us,'delay_us':s.delay_us,'frame_index':s.frame_index} for im,s in ims],
                    example=dict(meta,truth_hz=truth,baseline_hz=int(self.config.grid[baseline(features).argmin()])))

    def metrics(self,model,x,y):
        preds=[];tops=[]
        model.eval()
        with torch.no_grad():
            for i in range(0,len(y),64):
                self.check();scores=model(torch.from_numpy(x[i:i+64]))
                preds.extend(scores.argmax(1).tolist());tops.extend(scores.topk(min(5,scores.shape[1]),dim=1).indices.tolist())
        p=np.asarray(preds);base=np.array([baseline(a).argmin() for a in x]);grid=self.config.grid
        return {'count':len(y),'top1':float(np.mean(p==y)), 'top5':float(np.mean([label in row for label,row in zip(y,tops)])),
                'baseline_top1':float(np.mean(base==y)), 'candidate_coverage':float(np.mean(x[np.arange(len(y)),y,4]>.5)),
                'mean_error_hz':float(np.mean(np.abs(grid[p]-grid[y]))),
                'bands':{name:{'count':int(mask.sum()),'top1':float(np.mean(p[mask]==y[mask])) if mask.any() else None}
                         for name,mask in [('1–10k',grid[y]<=10000),('10–50k',(grid[y]>10000)&(grid[y]<=50000)),('50–100k',grid[y]>50000)]},
                'wrong_examples':[{'truth_hz':int(grid[y[i]]),'predicted_hz':int(grid[p[i]]),'baseline_hz':int(grid[base[i]])} for i in np.where(p!=y)[0][:20]]}

    def work(self,action):
        training=False
        try:
            if action=='audit':
                c=self.config;rows=[]
                # These examples are display-only; never update learned weights.
                for i,f in enumerate((1000,3000,26000,81000,90100,100000)):
                    if f not in c.grid:continue
                    self.check();x,meta,ims=experiment(c,f,c.seed+8000+i,self.check,True)
                    self.show(ims,meta,f,x)
                    rows.append(dict(meta,truth_hz=f,baseline_hz=int(c.grid[baseline(x).argmin()]),fit_error=float(baseline(x).min())))
                    self.update(audit=rows,message=f'观测检查 {len(rows)} 个案例；权重未更新')
                self.update(state='done',message='观测检查完成；权重未更新');return
            if action=='evaluate':
                model,_=self.load(best=True);x,y=self.dataset('test')
                result=self.metrics(model,x,y)
                self.record({'stage':'test','epoch':self.epoch,**result})
                (self.directory/'test_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                self.update(state='done',test=result,message='独立测试完成；best.pt 权重未更新');return
            self.model,self.optimizer=self.load();training=True
            x,y=self.dataset('train');vx,vy=self.dataset('validation')
            started=time.monotonic();deadline=started+self.config.max_minutes*60 if self.config.max_minutes else math_inf()
            while self.epoch<self.config.epochs:
                self.check()
                if time.monotonic()>=deadline:break
                order=np.random.default_rng([self.config.seed,self.epoch,444]).permutation(len(y))
                self.model.train();losses=[]
                while self.cursor<len(y):
                    self.check()
                    if time.monotonic()>=deadline:break
                    ids=order[self.cursor:self.cursor+self.config.batch_size]
                    logits=self.model(torch.from_numpy(x[ids]))
                    # A label pruned by the pixel candidate stage is unlearnable:
                    # report coverage, and exclude it from the ranking loss.
                    included=x[ids,y[ids],4]>.5
                    if included.any():
                        loss=torch.nn.functional.cross_entropy(logits[included],torch.from_numpy(y[ids][included]).long())
                        self.optimizer.zero_grad();loss.backward();self.optimizer.step();losses.append(float(loss.detach()))
                    self.cursor+=len(ids)
                    self.update(epoch=self.epoch,cursor=self.cursor,message=f'训练 epoch {self.epoch+1}，{self.cursor}/{len(y)}',elapsed_minutes=(time.monotonic()-started)/60)
                    if self.cursor% (self.config.batch_size*20)==0:self.save()
                if self.cursor<len(y):break
                self.epoch+=1;self.cursor=0;self.save()
                result=self.metrics(self.model,vx,vy)
                row={'stage':'validation','epoch':self.epoch,'loss':float(np.mean(losses)) if losses else None,**result}
                if result['top1']>self.best:
                    self.best=result['top1'];self.save('best.pt')
                self.save();self.record(row);self.update(validation=result,epoch=self.epoch)
            self.save();self.update(state='done',message='训练已结束并保存；可继续训练')
        except Cancelled:
            if training and self.model is not None:self.save()
            self.update(state='stopped',message='已停止；训练进度已保存' if training else '任务已停止；权重未更新')
        except Exception as e:
            if training and self.model is not None:self.save()
            self.update(state='error',message=str(e),error=type(e).__name__+': '+str(e))
        finally:
            if self.directory.exists():
                (self.directory/'status.json').write_text(json.dumps(self.public(),ensure_ascii=False,indent=2),encoding='utf-8')


def math_inf():return float('inf')


def handler(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*unused):pass
        def send(self,data,status=200,kind='application/json; charset=utf-8'):
            body=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/':self.send(Path(__file__).with_name('gated.html').read_bytes(),kind='text/html; charset=utf-8')
            elif self.path=='/api/state':self.send(session.public())
            else:self.send({'error':'Not found'},404)
        def do_POST(self):
            allowed={f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed or self.headers.get('Origin') not in (None,*['http://'+v for v in allowed]):
                self.send({'error':'Local same-origin requests only'},403);return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<=n<=8192:raise ValueError('Invalid size')
                data=json.loads(self.rfile.read(n) or b'{}')
                if self.path=='/api/stop':session.stop()
                elif self.path in ('/api/train','/api/audit','/api/evaluate'):
                    session.start(Config(**data),self.path.rsplit('/',1)[1])
                else:self.send({'error':'Not found'},404);return
                self.send({'ok':True})
            except (ValueError,TypeError) as e:self.send({'error':str(e)},400)
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=Path(__file__).parent/'gated_runs'/'fixed_10ms_v1')
    parser.add_argument('--port',type=int,default=8776)
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--headless',choices=('audit','train','evaluate'))
    for k,v in asdict(Config()).items():parser.add_argument('--'+k.replace('_','-'),type=type(v),default=v)
    args=parser.parse_args()
    if not 1<=args.port<=65535 or not 1<=args.threads<=32:parser.error('Invalid port or threads')
    torch.set_num_threads(args.threads)
    config=Config(**{k:getattr(args,k) for k in asdict(Config())}).validate()
    session=Session(args.run,config)
    if args.headless:
        session.start(session.config,args.headless)
        try:
            while session.worker.is_alive():session.worker.join(.3)
        except KeyboardInterrupt:
            session.stop();session.worker.join()
        print(json.dumps({k:v for k,v in session.public().items() if k not in ('preview','history')},ensure_ascii=False))
        return int(session.state['state']=='error')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(session));server.timeout=.4
    stopping=False
    def stop(*unused):
        nonlocal stopping
        stopping=True;session.stop()
    signal.signal(signal.SIGINT,stop)
    if hasattr(signal,'SIGTERM'):signal.signal(signal.SIGTERM,stop)
    print(f'IDLE. Open http://127.0.0.1:{args.port}/\nRun: {session.directory}\nClick Train yourself; Ctrl+C saves and exits.',flush=True)
    try:
        while not stopping:server.handle_request()
    finally:
        session.stop();server.server_close()
        if session.worker:session.worker.join()
    return 0


if __name__=='__main__':raise SystemExit(main())
