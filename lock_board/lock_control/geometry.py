import math
import numpy as np
from functools import lru_cache
from .pixels import foreground

def target_curve(mode, height, samples=512):
    angle = np.linspace(0, 2*np.pi, samples, endpoint=False)
    x = 4*np.sin(angle)
    y = height/2*(np.sin(angle) if mode=='diagonal' else
                  np.cos(angle) if mode=='circle' else np.sin(2*angle))
    return x, y


@lru_cache(maxsize=12)
def target_distance(mode, height, width, rows, bounds):
    xmin,ymin,xmax,ymax = bounds
    x = np.linspace(xmin,xmax,width,dtype=np.float32)[None,:]
    y = np.linspace(ymax,ymin,rows,dtype=np.float32)[:,None]
    distance = np.full((rows,width),np.inf,np.float32)
    tx,ty = target_curve(mode,height)
    for a,b in zip(tx,ty):
        a,b=np.float32(a),np.float32(b)
        np.minimum(distance,(x-a)**2+(y-b)**2,out=distance)
    np.sqrt(distance,out=distance)
    return distance,tx,ty


def shape_error(image,mode,height_div=8):
    # Hold verification compares the known target contour directly. A crossing
    # need not be decomposable into two branches to prove its shape is correct.
    mask=foreground(image);ys,xs=np.where(mask)
    if len(xs)<100 or min(xs.min(),ys.min())<=1 or max(xs.max(),ys.max())>=382:return None
    distance,tx,ty=target_distance(mode,height_div,384,384,(-5.,-5.,5.,5.))
    error=float(np.percentile(distance[mask],95))
    # Reverse coverage prevents a single point/partial arc on the template from
    # masquerading as a complete target. Tolerance stays 0.2 div.
    px=np.clip(np.rint((tx+5)*38.3).astype(int),0,383);py=np.clip(np.rint((5-ty)*38.3).astype(int),0,383)
    integral=np.pad(mask.astype(np.int32),((1,0),(1,0))).cumsum(0).cumsum(1)
    x0=np.maximum(px-8,0);x1=np.minimum(px+8,383)+1;y0=np.maximum(py-8,0);y1=np.minimum(py+8,383)+1
    covered=integral[y1,x1]-integral[y0,x1]-integral[y1,x0]+integral[y0,x0]>0
    if np.mean(covered)<.95:return max(error,.201)
    return error

