"""Persistent JSON-line IPC for C/C++ and other board programs."""
import argparse,json,sys
from PIL import Image
from . import LockController,Signal,Frame,Options

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-hz',type=float,required=True)
    p.add_argument('--mode',choices=('circle','diagonal','infinity'),default='circle')
    p.add_argument('--continuous-phase',action='store_true')
    p.add_argument('--fixed',action='store_true')
    p.add_argument('--frequency-step',type=float,default=.0001)
    p.add_argument('--settle',type=float,default=.02)
    p.add_argument('--latency',type=float,default=.01)
    args=p.parse_args()
    ctrl=LockController(args.source_hz,args.mode,Options(frequency_step_hz=args.frequency_step,settle_s=args.settle,command_latency_s=args.latency),not args.fixed,args.continuous_phase)
    for line in sys.stdin:
        try:
            d=json.loads(line)
            if d['op']=='start':r=ctrl.start(Signal(**d['signal']),d['command_id'],d['applied_at_s'],d.get('now_s'))
            elif d['op']=='applied':r=ctrl.applied(d['command_id'],Signal(**d['signal']),d['applied_at_s'])
            elif d['op']=='retime':
                print(json.dumps({'command':ctrl.retime_pending(d['apply_at_s']).to_dict()},ensure_ascii=False),flush=True);continue
            elif d['op']=='frame':
                with Image.open(d['image_path']) as im:image=im.convert('RGB').copy()
                r=ctrl.step(Frame(image,Signal(**d['signal']),d['frame_id'],d['command_id'],d['captured_at_s'],d['calibration_id']),d['now_s'])
            else:raise ValueError('Unknown operation')
            print(json.dumps(r.to_dict(),ensure_ascii=False,allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'status':'error','reason':str(e)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
