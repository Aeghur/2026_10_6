"""Rank nearby sine frequencies by within-frame Lissajous coherence.

Changing phase between frames is allowed at any speed. Only the complexity
inside each captured image is scored; it is not a phase-lock controller.
"""
import cv2
import numpy as np


def candidates(center):
    if center%100:raise ValueError('Coarse center must be on the 100 Hz grid')
    return [int(center+d)for d in (-200,-100,0,100,200)if 1000<=center+d<=100000]


def score_image(image):
    if image.ndim!=2:raise ValueError('Expected thresholded grayscale mask')
    # Ignore screen frame, interface and extreme amplitude clipping bands.
    mask=image>=100
    mask=mask.copy();mask[:20]=False;mask[-20:]=False;mask[:,:80]=False;mask[:,880:]=False
    y,x=np.where(mask)
    if len(x)<300:raise ValueError('Insufficient sine trace')
    x0,x1=np.quantile(x,[.005,.995]);y0,y1=np.quantile(y,[.005,.995])
    if x1-x0<150 or y1-y0<150:raise ValueError('Insufficient signal span')
    inside=(x>=x0)&(x<=x1)&(y>=y0)&(y<=y1)
    x=x[inside].astype(float);y=y[inside].astype(float)
    density=float(len(x)/max(1,(x1-x0+1)*(y1-y0+1)))
    if len(x)>5000:
        keep=np.random.default_rng(0).choice(len(x),5000,replace=False);x=x[keep];y=y[keep]
    X=(x-(x0+x1)/2)/((x1-x0)/2);Y=(y-(y0+y1)/2)/((y1-y0)/2)
    # A single-frequency sine pair obeys
    # X²+Y²-2*rho*X*Y = 1-rho², rho=cos(relative phase).
    # rho may differ arbitrarily between frames; rho=±1 permits a line.
    base=X*X+Y*Y-1;cross=2*X*Y
    best=(float('inf'),0.)
    for rho in np.linspace(-1,1,401):
        residual=base-rho*cross+rho*rho
        rms=float(np.sqrt(np.mean(residual**2)))
        if rms<best[0]:best=rms,float(rho)
    rms,rho=best
    return dict(score=rms+.5*density,conic_rms=rms,fill_fraction=density,
                fitted_phase_cosine=rho,bounds=[float(x0),float(y0),float(x1),float(y1)],
                foreground_count=int(inside.sum()),phase_change_between_frames_penalized=False)


def choose(rows):
    usable=[r for r in rows if len(r.get('frames',[]))>=3]
    ranked=[]
    for row in usable:
        values=[v['metrics']['score']for v in row['frames']if'metrics'in v]
        if len(values)<3:continue
        ranked.append(dict(frequency_hz=row['frequency_hz'],score=float(np.median(values)),
                           frame_scores=values))
    ranked.sort(key=lambda r:r['score'])
    if len(ranked)<2:return dict(status='insufficient_candidates',frequency_hz=None,ranking=ranked)
    best,second=ranked[:2]
    ratio=second['score']/max(best['score'],.015)
    separated=best['score']<.18 and ratio>=1.8
    return dict(status='frequency_candidate'if separated else'ambiguous',
                frequency_hz=best['frequency_hz']if separated else None,
                provisional_best_hz=best['frequency_hz'],runner_up_to_best_ratio=ratio,
                ranking=ranked,confirmed=False,
                criterion='Median within-frame ellipse/line residual plus occupancy; no interframe phase-speed penalty')
