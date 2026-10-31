"""Finite-pixel, fixed-repetition gated-ramp frequency experiment (independent V5).

Inference accepts rectified bitmaps and public scan timing only. Private source
frequency is used exclusively by render() and supervised training/evaluation.
"""
from dataclasses import dataclass
import math
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import torch
from torch import nn

VERSION = 'gated-ramp-1'
SIZE = 384
TOP, BOTTOM = 38, 345
ROWS = np.linspace(TOP + 6, BOTTOM - 6, 128).round().astype(int)
FRACTIONS = (BOTTOM - ROWS) / (BOTTOM - TOP)
BANK = (50., 100., 200., 500., 1000., 2000., 4000.)


@dataclass
class Config:
    minimum_hz: int = 1000
    maximum_hz: int = 100000
    step_hz: int = 100
    repeat_us: float = 10000.
    clock_ppm: float = 5.
    exposure_ms: float = 60.
    command_gap_ms: float = 120.
    line_width: int = 3
    seed: int = 20261031
    train_cases: int = 1024
    validation_cases: int = 256
    test_cases: int = 512
    epochs: int = 10000
    batch_size: int = 32
    learning_rate: float = .001
    max_minutes: float = 60.

    @property
    def grid(self):
        return np.arange(self.minimum_hz, self.maximum_hz + 1, self.step_hz)

    def validate(self):
        for k in ('minimum_hz','maximum_hz','step_hz','line_width','seed','train_cases','validation_cases','test_cases','epochs','batch_size'):
            if type(getattr(self,k)) is not int:
                raise ValueError(k + ' must be an integer')
        if self.step_hz != 100 or not 1000 <= self.minimum_hz < self.maximum_hz <= 100000 or self.minimum_hz % 100 or self.maximum_hz % 100:
            raise ValueError('Frequency range: 1000..100000 Hz on the 100 Hz grid')
        if self.repeat_us != 10000:
            raise ValueError('This experiment uses a fixed 10000 us repetition period')
        for k in ('clock_ppm','exposure_ms','command_gap_ms','learning_rate','max_minutes'):
            if not math.isfinite(getattr(self,k)):
                raise ValueError(k + ' must be finite')
        if not 0 <= self.clock_ppm <= 100 or not 10 <= self.exposure_ms <= 300 or not self.exposure_ms <= self.command_gap_ms <= 1000:
            raise ValueError('ppm 0..100; exposure 10..300 ms; gap >= exposure and <=1000 ms')
        if not 1 <= self.line_width <= 5 or self.seed < 0 or min(self.train_cases,self.validation_cases,self.test_cases,self.epochs,self.batch_size) < 1:
            raise ValueError('Positive counts; line width 1..5; nonnegative seed')
        if max(self.train_cases,self.validation_cases,self.test_cases)>100000 or self.batch_size>1024 or not 0<self.learning_rate<=.1 or not 0<=self.max_minutes<=1440:
            raise ValueError('Counts <=100000; batch <=1024; lr (0,.1]; minutes 0..1440')
        return self


@dataclass(frozen=True)
class Scan:
    active_us: float
    delay_us: float = 0.
    frame_index: int = 0  # index in the continuously running 10 ms superframe

    def validate(self):
        if not all(math.isfinite(x) for x in (self.active_us,self.delay_us)) or not 0 < self.active_us <= 4000 or not 0 <= self.delay_us <= 10000-self.active_us or type(self.frame_index) is not int or self.frame_index<0:
            raise ValueError('Invalid public scan timing')
        return self


class Scene:
    """Relative source/DAC clock error; shared geometry and continuous time."""
    def __init__(self, frequency, config, seed):
        self.config = config
        self.rng = np.random.default_rng(seed)
        self.frequency = frequency
        self.actual = frequency*(1+self.rng.uniform(-config.clock_ppm,config.clock_ppm)*1e-6)
        self.phase = self.rng.uniform(0,2*np.pi)
        self.amplitude = self.rng.uniform(125,151)
        self.center = 191.5+self.rng.uniform(-2,2)
        self.width = int(self.rng.integers(1,config.line_width+1))
        self.blur = self.rng.uniform(0,.6)

    def render(self, scan):
        scan.validate()
        im = Image.new('L',(SIZE,SIZE),0); d = ImageDraw.Draw(im)
        # Integrate whole repeated sweeps (including relative-clock drift).
        n = max(1,math.ceil(self.config.exposure_ms/10))
        for k in range(n):
            start = (scan.frame_index+k)*.01+scan.delay_us*1e-6
            t = np.linspace(0,scan.active_us*1e-6,max(640,int(self.actual*scan.active_us*1e-6*30)))
            x = self.center+self.amplitude*np.sin(2*np.pi*self.actual*(start+t)+self.phase)
            y = BOTTOM-(BOTTOM-TOP)*t/(scan.active_us*1e-6)
            d.line(np.column_stack((x,y)).ravel().tolist(),fill=235,width=self.width)
        # Holding the ramp at its maximum produces a real bright endpoint line.
        d.line((self.center-self.amplitude,TOP,self.center+self.amplitude,TOP),fill=255,width=self.width+2)
        a=np.asarray(im,np.float32).copy()
        for p in np.linspace(0,SIZE-1,11).round().astype(int):
            a[p,:]=np.maximum(a[p,:],18);a[:,p]=np.maximum(a[:,p],18)
        im=Image.fromarray(a.astype('uint8')).filter(ImageFilter.GaussianBlur(self.blur))
        a=np.asarray(im,np.float32)+self.rng.normal(0,2,(SIZE,SIZE))
        return Image.fromarray(np.clip(a,0,255).astype('uint8'))


def profile(image):
    """Known calibrated ramp endpoints, never crop by the unknown sine bbox."""
    if image.size != (SIZE,SIZE):
        raise ValueError('Expected rectified 384x384 image; ramp endpoints y=38,345')
    a=np.asarray(image.convert('L'),np.float32)[ROWS]
    mask=a>max(75,float(np.percentile(a,99.5))*.45)
    counts=mask.sum(1); good=counts>0
    x=(mask*np.arange(SIZE)[None]).sum(1)/np.maximum(counts,1)
    x=(x-191.5)/153.5
    x[~good]=0
    # No interpolation through missing rows: they have zero fit weight.
    return x,good.astype(float),np.array([good.mean(),np.median(counts)/SIZE,np.std(x[good]) if good.any() else 0])


def fit(frequencies, times, values, weights):
    """Common sin/cos phase and DC across frames; phase is a fitted nuisance."""
    angle=2*np.pi*np.asarray(frequencies)[:,None]*times[None]
    b=np.stack((np.sin(angle),np.cos(angle),np.ones_like(angle)),axis=2)
    gram=np.einsum('fna,fnb,n->fab',b,b,weights)+np.eye(3)[None]*1e-6
    rhs=np.einsum('fna,n,n->fa',b,values,weights)
    coef=np.linalg.solve(gram,rhs[...,None])[...,0]
    error=np.maximum(0,np.sum(weights*values**2)-np.sum(coef*rhs,axis=1))/max(1,weights.sum())
    amp=np.hypot(coef[:,0],coef[:,1])
    return error,amp


def local_scores(image, scan, grid):
    values,weights,quality=profile(image)
    error,amp=fit(grid,FRACTIONS*scan.active_us*1e-6,values,weights)
    cycles=grid*scan.active_us*1e-6
    # Dense/short nearly-flat curves cannot masquerade as a lower-frequency sine.
    score=error+np.maximum(0,.55-amp)**2+np.maximum(0,amp-1.15)**2
    score[(cycles<.65)|(cycles>8)|(quality[0]<.9)]=10
    return score,quality


def extract(observations, grid, clock_ppm):
    """No Scene/configured source labels cross this inference boundary."""
    values=[];weights=[];times=[];qualities=[];local=[]
    for image,scan in observations:
        scan.validate()
        x,w,q=profile(image);values.extend(x);weights.extend(w);qualities.append(q)
        times.extend(scan.frame_index*.01+scan.delay_us*1e-6+FRACTIONS*scan.active_us*1e-6)
        local.append(local_scores(image,scan,grid)[0])
    local=np.min(local,axis=0)
    # Restrict expensive coherent fits using image-only scouts; report coverage.
    seeds=np.argsort(local)[:12]
    candidates=np.unique(np.clip((seeds[:,None]+np.arange(-3,4)).ravel(),0,len(grid)-1))
    offsets=np.linspace(-clock_ppm,clock_ppm,9)
    hypotheses=(grid[candidates,None]*(1+offsets[None]*1e-6)).ravel()
    err,amp=fit(hypotheses,np.asarray(times),np.asarray(values),np.asarray(weights))
    e=(err+np.maximum(0,.55-amp)**2+np.maximum(0,amp-1.15)**2).reshape(-1,9)
    best=e.argmin(1); coherent=e[np.arange(len(candidates)),best]
    features=np.zeros((len(grid),5),np.float32)
    features[:,0]=np.minimum(local,2)
    features[:,1]=2
    features[candidates,1]=np.minimum(coherent,2)
    features[candidates,2]=amp.reshape(-1,9)[np.arange(len(candidates)),best]
    features[:,3]=float(np.mean([q[1] for q in qualities]))
    features[candidates,4]=1
    return features


def baseline(features):
    score=features[:,1]+.15*features[:,0]
    return np.where(features[:,4]>0,score,np.inf)


def experiment(config, frequency, seed, check=lambda:None, preview=False):
    scene=Scene(frequency,config,seed);observations=[]
    gap=max(1,math.ceil(config.command_gap_ms/10))
    for i,t in enumerate((50.,250.,2000.)):
        check();s=Scan(t,0,i*gap);observations.append((scene.render(s),s))
    scout=np.min([local_scores(im,s,config.grid)[0] for im,s in observations],axis=0)
    coarse=int(config.grid[scout.argmin()])
    active=min(BANK,key=lambda t:abs(math.log(max(1e-6,coarse*t*1e-6/4))))
    # All commands preserve the same 10 ms epoch, including across delay changes.
    fine=[]
    for i,delay in enumerate((0.,1700.,4300.)):
        check();s=Scan(active,delay,(i+3)*gap);fine.append((scene.render(s),s))
    # Coarse frames select candidates; only the matched, sparse fine frames
    # enter coherent phase fitting. Dense scout profiles must not pollute it.
    features=extract(fine,config.grid,config.clock_ppm)
    return features,{'coarse_hz':coarse,'active_us':active,'commands':6},(observations+fine if preview else [])


class Ranker(nn.Module):
    """Shared candidate scorer, trained on pixels-derived fit features only."""
    def __init__(self):
        super().__init__()
        self.scale=nn.Parameter(torch.tensor(4.))
        self.net=nn.Sequential(nn.Linear(4,24),nn.Tanh(),nn.Linear(24,1))
        nn.init.zeros_(self.net[-1].weight);nn.init.zeros_(self.net[-1].bias)

    def forward(self,x):
        physical=x[...,1]+.15*x[...,0]
        score=-torch.nn.functional.softplus(self.scale)*physical*30+self.net(x[...,:4]).squeeze(-1)
        return score.masked_fill(x[...,4]<.5,-1e4)
