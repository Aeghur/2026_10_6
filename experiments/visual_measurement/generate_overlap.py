"""Fit an illustrative union of phase-shifted sine sweeps; no hardware access.

Phase step is fitted independently of visible cycle count because the full ramp
height/time mapping is not supplied. This is an image model, not a frequency
measurement or a calibrated acquisition simulator.
"""
from pathlib import Path
import argparse
import json
import cv2
import numpy as np


def render(shape, bounds, params, samples=700):
    cycles, phase, step, count, thickness = params
    left, top, right, bottom = bounds
    t = np.linspace(0, 1, samples)
    center = (left+right)/2
    radius = max(1, (right-left-thickness)/2)
    result = np.zeros(shape, np.uint8)
    for k in range(int(round(count))):
        x = center + radius*np.sin(2*np.pi*cycles*t+phase+k*step)
        y = top+(bottom-top)*t
        points = np.rint(np.column_stack((x,y))).astype(np.int32)
        layer = np.zeros(shape, np.uint8)
        cv2.polylines(layer,[points],False,255,max(1,int(round(thickness))),cv2.LINE_AA)
        np.maximum(result,layer,out=result)
    return result


def fit(source, seed=42, single_sweep=False):
    scale=0.5
    target=cv2.resize(source,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    mask=target>=100
    yy,xx=np.where(mask)
    bounds=[float(xx.min()),float(yy.min()),float(xx.max()),float(yy.max())]
    # Symmetric pixel disagreement penalizes curves in empty gaps as well as
    # observed foreground not explained by the generated union.
    def loss(p):
        predicted=render(target.shape,bounds,p,samples=500)>=100
        union=np.count_nonzero(predicted|mask)
        return 1-np.count_nonzero(predicted&mask)/max(union,1)
    rng=np.random.default_rng(seed)
    lower=np.array([.5,0,0,1,1.])
    upper=np.array([35.,2*np.pi,2*np.pi,9.,5.])
    if single_sweep:
        lower[2:4]=[0.,1.]
        upper[2:4]=[0.,1.]
    best=None; best_loss=2.
    for i in range(3500):
        p=rng.uniform(lower,upper)
        score=loss(p)
        if score<best_loss: best,best_loss=p,score
    for widths in ([1.5,.8,.8,1.5,1.],[.4,.3,.3,1.,.6],[.12,.1,.1,.7,.3],[.04,.04,.04,.4,.15]):
        for i in range(700):
            p=np.clip(best+rng.normal(size=5)*widths,lower,upper)
            score=loss(p)
            if score<best_loss: best,best_loss=p,score
    full_bounds=[x/scale for x in bounds]
    final=best.copy(); final[4]/=scale
    return render(source.shape,full_bounds,final,samples=1800),{
        'visible_cycles':float(best[0]),'first_phase_rad':float(best[1]),
        'phase_step_per_sweep_rad':float(best[2]),'sweep_count':int(round(best[3])),
        'line_thickness_px':float(final[4]),'bounds_px':full_bounds,
        'fit_iou_at_half_resolution':float(1-best_loss),
        'measurement_valid':False,
        'single_sweep_no_exposure_union':single_sweep,
        'limitations':['Image-space fit only; multiple parameter solutions may exist.',
                      'No verified full-ramp height or display persistence supplied.',
                      'Independent fitted sweep phase step; not yet constrained by DAC frequency.',
                      'No absolute input frequency or uniquely resolved phase is claimed.']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('image',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--single-sweep',action='store_true',help='One sine trace only; no exposure union')
    args=parser.parse_args()
    source=cv2.imread(str(args.image),cv2.IMREAD_GRAYSCALE)
    if source is None: raise ValueError('Cannot read input image')
    args.output.mkdir(parents=True,exist_ok=True)
    generated,report=fit(source,single_sweep=args.single_sweep)
    cv2.imwrite(str(args.output/'generated.png'),generated)
    # Gray=agreement, green=generated only, red=observed only.
    obs=source>=100; pred=generated>=100
    overlay=np.zeros((*source.shape,3),np.uint8)
    overlay[obs&pred]=(220,220,220)
    overlay[pred&~obs]=(0,220,0)
    overlay[obs&~pred]=(0,0,220)
    cv2.imwrite(str(args.output/'difference.png'),overlay)
    panels=[]
    for label,im in [('Observed',source),('Generated',generated),('Difference',overlay)]:
        if im.ndim==2: im=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)
        panel=cv2.copyMakeBorder(im,30,0,0,0,cv2.BORDER_CONSTANT)
        cv2.putText(panel,label,(10,21),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
        panels.append(panel)
    cv2.imwrite(str(args.output/'comparison.jpg'),np.hstack(panels))
    report['input']=str(args.image.resolve())
    (args.output/'fit.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
