"""Calibrated bitmap measurements. No signals, vector paths or frequency inputs."""
from dataclasses import dataclass
from functools import lru_cache
import math
import os

os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
os.environ['MKL_NUM_THREADS']='1'
import numpy as np
from PIL import Image

AMPLITUDE_TOLERANCE_DIV = .2
DRIFT_TOLERANCE_DIV = 1.
STABLE_SECONDS = 5.
CONTROL_LIMIT_SECONDS = 10.
# The exam gives no numeric static contour-distance tolerance. This is an
# explicit, adjustable engineering threshold, separate from amplitude/drift.
DEFAULT_SHAPE_TOLERANCE_DIV = .2


@dataclass(frozen=True)
class Observation:
    ratio: float | None
    confidence: float
    x_height: float
    y_height: float
    correlation: float
    infinity_moment: float
    shape_error: float
    crossings_x: int
    crossings_y: int
    density: float
    source: str = 'raster'
    coverage: float = 0.
    center_x: float = 0.
    center_y: float = 0.
    symmetry_error: float = 0.
    profile: tuple = ()
    family_error_div: float = 10.


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


def foreground(image):
    array = np.asarray(image)
    if array.ndim==3:
        array = array[:,:,:3].max(axis=2)
    if array.ndim!=2 or min(array.shape)<32:
        raise ValueError('Expected a calibrated grayscale/RGB plotting bitmap, at least 32px')
    mask = array > max(75,float(np.percentile(array,99.5))*.45)
    # Reject isolated hot/noise pixels, retain connected one-pixel curves.
    pad = np.pad(mask,1)
    neighbors = np.zeros(mask.shape,np.uint8)
    for dy in range(3):
        for dx in range(3):
            if (dy,dx)!=(1,1):
                neighbors += pad[dy:dy+mask.shape[0],dx:dx+mask.shape[1]]
    mask &= neighbors>=2
    return mask


def components(line):
    return int(np.count_nonzero(line & ~np.r_[False,line[:-1]]))


def observe_image(image,mode,height,bounds=(-5.,-5.,5.,5.),shape_tolerance=.2,viewport=None):
    """This is the actual controller input boundary: pixels plus calibration.

    Grid/UI/frame masking and a valid pixels-to-div calibration are required
    when substituting a real camera image for the synthetic bitmap.
    """
    mask=foreground(image)
    if viewport is not None:
        mask &= np.asarray(viewport)>0
    rows,width=mask.shape
    xmin,ymin,xmax,ymax=map(float,bounds)
    dx,dy=(xmax-xmin)/(width-1),(ymax-ymin)/(rows-1)
    yy,xx=np.where(mask)
    if not len(xx):
        return Observation(None,0,0,0,0,0,10,0,0,0,coverage=0)
    lo,hi=xx.min(),xx.max()
    top,bottom=yy.min(),yy.max()
    xheight,yheight=float((hi-lo)*dx),float((bottom-top)*dy)
    centerx=xmin+(hi+lo)*dx/2
    centery=ymax-(bottom+top)*dy/2
    counts_x,counts_y=[],[]
    for fraction in (.21,.33,.46,.61,.74):
        ix=round(lo+(hi-lo)*fraction)
        iy=round(top+(bottom-top)*fraction)
        counts_x.append(components(mask[:,ix]))
        counts_y.append(components(mask[iy,:]))
    cx,cy=int(np.median(counts_x)),int(np.median(counts_y))
    occupancy=float(mask[top:bottom+1,lo:hi+1].mean())
    spread=max(np.std(counts_x)/max(cx,1),np.std(counts_y)/max(cy,1))
    confidence=max(0.,1-spread)*max(0.,1-(occupancy-.12)/.5)
    confidence=float(np.clip(confidence,0,1)) if cx and cy else 0.
    ratio=float(cy/cx) if cx and cy and confidence>=.55 else None
    mids,us,gaps=[] ,[],[]
    profile=[]
    for column in np.linspace(lo,hi,41).round().astype(int):
        hit=np.flatnonzero(mask[:,column])
        if len(hit):
            y1,y2=ymax-hit[0]*dy,ymax-hit[-1]*dy
            profile.extend((float(y1),float(y2)))
            u=(xmin+column*dx-centerx)/max(xheight/2,.05)
            if abs(u)<.87:
                us.append(u);mids.append(((y1+y2)/2-centery)/max(yheight/2,.05))
                gaps.append((y1-y2)/max(yheight,.1))
        else:
            profile.extend((None,None))
    u=np.array(us);middle=np.array(mids)
    # Drift landmarks use fixed calibrated X coordinates, not a moving bbox.
    profile=[]
    for column in np.clip(np.round((np.linspace(-3.8,3.8,41)-xmin)/dx).astype(int),0,width-1):
        hit=np.flatnonzero(mask[:,column])
        profile.extend((float(ymax-hit[0]*dy),float(ymax-hit[-1]*dy)) if len(hit) else (None,None))
    cosphase=float(np.sum(u*middle)/max(np.sum(u*u),1e-9))
    basis=1-2*u*u
    sinphase=float(np.sum(basis*middle)/max(np.sum(basis*basis),1e-9))
    if mode=='infinity':
        fitted=np.clip(sinphase,-1,1)
        predicted_mid=fitted*basis
        predicted_gap=np.abs(2*u)*np.sqrt(np.maximum(0,1-u*u))*np.sqrt(1-fitted*fitted)
    else:
        fitted=np.clip(cosphase,-1,1)
        predicted_mid=fitted*u
        predicted_gap=np.sqrt(np.maximum(0,1-u*u))*np.sqrt(1-fitted*fitted)
    family_error=float(np.percentile(np.maximum(np.abs(middle-predicted_mid),
                         np.abs(np.array(gaps)-predicted_gap)),95)*yheight/2) if len(middle) else 10.
    distance,tx,ty=target_distance(mode,float(height),width,rows,tuple(map(float,bounds)))
    error=float(np.percentile(distance[mask],99))
    # Reverse template coverage prevents accepting a tiny matching fragment.
    xp=np.clip(np.round((tx-xmin)/dx).astype(int),0,width-1)
    yp=np.clip(np.round((ymax-ty)/dy).astype(int),0,rows-1)
    radius=max(1,int(shape_tolerance/max(dx,dy)/math.sqrt(2)))
    integral=np.pad(mask.astype(np.int32),((1,0),(1,0))).cumsum(0,dtype=np.int32)
    np.cumsum(integral,axis=1,dtype=np.int32,out=integral)
    xa,xb=np.maximum(0,xp-radius),np.minimum(width,xp+radius+1)
    ya,yb=np.maximum(0,yp-radius),np.minimum(rows,yp+radius+1)
    local=integral[yb,xb]-integral[ya,xb]-integral[yb,xa]+integral[ya,xa]
    coverage=float(np.mean(local>0))
    symmetry=float(np.percentile(np.abs(middle),95)*yheight/2) if len(middle) else 10.
    return Observation(ratio,confidence,xheight,yheight,float(np.clip(cosphase,-1,1)),
        float(np.clip(sinphase,-1,1)),error,cx,cy,float(mask.mean()),'raster',coverage,
        float(centerx),float(centery),symmetry,tuple(profile),family_error)


def visually_ready(obs,mode,height,shape_tolerance=DEFAULT_SHAPE_TOLERANCE_DIV):
    # No exact frequency gate: an accepted pixel contour is the criterion.
    return (abs(obs.y_height-height)<=AMPLITUDE_TOLERANCE_DIV and
            obs.shape_error<=shape_tolerance and obs.coverage>=.98 and
            (mode!='infinity' or obs.symmetry_error<=shape_tolerance))


def drift_between(first,current):
    pairs=[abs(a-b) for a,b in zip(first.profile,current.profile)
           if a is not None and b is not None]
    return max([abs(first.center_x-current.center_x),abs(first.center_y-current.center_y)]+pairs)
