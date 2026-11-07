"""Line-oriented JSON IPC for C/C++/other board applications."""
import argparse
import json
import sys
from PIL import Image
from . import PhaseController,Signal,Frame,Options


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-hz',type=float,required=True)
    p.add_argument('--mode',choices=('circle','diagonal','infinity'),default='circle')
    p.add_argument('--continuous-phase',action='store_true')
    p.add_argument('--target-height',type=int,choices=(2,4,6,8))
    p.add_argument('--settle-s',type=float,default=.08)
    p.add_argument('--latency-s',type=float,default=.02)
    args=p.parse_args()
    controller=PhaseController(args.source_hz,args.mode,Options(settle_s=args.settle_s,command_latency_s=args.latency_s),args.target_height,args.continuous_phase)
    for line in sys.stdin:
        try:
            data=json.loads(line);op=data['op']
            if op=='start':r=controller.start(Signal(**data['signal']),data['command_id'],data['applied_at_s'])
            elif op=='applied':r=controller.applied(data['command_id'],Signal(**data['signal']),data['applied_at_s'])
            elif op=='retime':
                print(json.dumps({'command':controller.retime_pending(data['apply_at_s']).to_dict()},ensure_ascii=False),flush=True);continue
            elif op=='frame':
                with Image.open(data['image_path']) as im:image=im.convert('RGB').copy()
                frame=Frame(image,Signal(**data['signal']),data['frame_id'],data['command_id'],data['captured_at_s'],data['calibration_id'])
                r=controller.step(frame,data['now_s'])
            else:raise ValueError('Unknown op')
            print(json.dumps(r.to_dict(),ensure_ascii=False,allow_nan=False),flush=True)
        except Exception as e:print(json.dumps({'status':'error','reason':str(e)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
