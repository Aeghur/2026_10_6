"""Single-latched + asynchronous frequency experts, with pixel-only routing.

Acquisition labels are training/evaluation targets only. Inference does not
accept them. A clear trace is geometric evidence, not proof of hardware mode.
"""
from dataclasses import dataclass, asdict
import math
import numpy as np
import torch
from torch import nn

from .multisweep_model import (MultiConfig, MultiNet, MultiPlant, metadata, encode_pair,
                              capture_pair as capture_free_pair, train_losses, frequency_result)
from .sweep_vision import estimate_sweep
from .pixel_vision import foreground

VERSION = 'hybrid-frequency-3.0'
SPLITS = {'train':81001,'validation':82003,'calibration':83009,'test':84011,'audit':85013}
MODES = ('single','free')


@dataclass
class HybridConfig(MultiConfig):
    windows_us: str = '73,110,175,223,809,1750,3500'
    single_fraction: float = .5
    freeze_continuous: int = 1
    continuous_windows_us: str = '73,110,175,223,809,1750'
    max_minutes: float = 0.

    @property
    def continuous_windows(self):
        return tuple(float(x.strip()) for x in self.continuous_windows_us.split(','))

    def validate(self):
        super().validate()
        if not math.isfinite(self.max_minutes) or self.max_minutes < 0:
            raise ValueError('max_minutes must be finite and >= 0 (0 = unlimited)')
        if not math.isfinite(self.single_fraction) or not .1 <= self.single_fraction <= .9:
            raise ValueError('single_fraction must be .1.. .9')
        if self.train_per_frequency < 2:
            raise ValueError('At least 2 training scenes per frequency, covering both modes')
        if self.freeze_continuous not in (0,1):
            raise ValueError('freeze_continuous must be 0 or 1')
        if not {175.,1750.}.issubset(self.continuous_windows) or not set(self.continuous_windows).issubset(self.windows):
            raise ValueError('continuous_windows_us must be a subset including 175 and 1750')
        for name in ('validation_cases','calibration_cases','test_cases'):
            if getattr(self,name)%2:
                raise ValueError(name+' must be even: paired single/free evaluation')
        return self


def plan(config, split):
    """Balanced per-frequency training; paired, shuffled held-out evaluation."""
    rng = np.random.default_rng([config.seed,SPLITS[split]])
    n = len(config.frequencies)
    if split == 'train':
        copies = config.train_per_frequency
        singles = min(copies-1,max(1,round(copies*config.single_fraction)))
        assignments = np.zeros((n,copies),np.int64)
        for row in assignments:
            row[rng.choice(copies,singles,replace=False)] = 1
        entries = [(int(label),0 if assignments[label,rep] else 1)
                   for rep in range(copies) for label in rng.permutation(n)]
    else:
        count = getattr(config,split+'_cases')//2
        labels = np.concatenate([rng.permutation(n) for _ in range(math.ceil(count/n))])[:count]
        entries = [(int(label),mode) for label in labels for mode in (0,1)]
        rng.shuffle(entries)
    for i,(label,mode) in enumerate(entries):
        seed = np.random.SeedSequence([config.seed,SPLITS[split],i]).generate_state(4).tolist()
        yield label,mode,seed


def make_plant(config,seed,frequency,mode):
    if mode not in MODES:
        raise ValueError('Unknown acquisition mode')
    rng = np.random.default_rng(seed)
    plant = MultiPlant(int(frequency),float(rng.uniform(0,360)),seed=int(rng.integers(0,2**31-1)),
        domain=config.domain,acquisition=mode,exposure_ms=config.exposure_ms,persistence_ms=config.persistence_ms)
    plant.advance(float(rng.uniform(.02,.3)))
    return plant,rng


def capture_pair(plant,window_us,rng,check=lambda:None):
    images,count=capture_free_pair(plant,window_us,rng,check)
    # A latched scope may display the same acquisition in both camera frames.
    # Train on both held-frame and rearmed-latch pairs. This condition belongs
    # to the simulator, and is not passed to the recognizer as metadata.
    if plant.acquisition=='single' and rng.random()<.5:
        images[1]=images[0].copy()
    return images,count


def fit_single_pair(images,window,config):
    """FFT localizes the search; phase/amplitude are profiled out by LS.

    Values are coarse geometric evidence, never an exact 100 Hz label. Pixel
    endpoint quantization is explicitly included in the engineering interval.
    """
    estimates=[]
    for image in images:
        mask=foreground(image)
        yy,xx=np.where(mask)
        if len(yy)<50:
            return np.zeros(6,np.float32)
        top,bottom=int(yy.min()),int(yy.max())
        cropped=mask[top:bottom+1]
        counts=cropped.sum(1)
        good=counts>0
        if bottom-top<100 or good.mean()<.9:
            return np.zeros(6,np.float32)
        profile=(cropped*np.arange(mask.shape[1])[None]).sum(1)/np.maximum(counts,1)
        profile=np.interp(np.arange(len(profile)),np.flatnonzero(good),profile[good])[::-1]
        spectrum=np.abs(np.fft.rfft((profile-profile.mean())*np.hanning(len(profile)),n=4096))
        cycles=np.arange(len(spectrum))*len(profile)/4096
        allowed=(cycles>=.8)&(cycles<=min((len(profile)-1)/5,config.max_frequency*window*1e-6))
        if not allowed.any():
            return np.zeros(6,np.float32)
        peak=float(cycles[np.flatnonzero(allowed)[np.argmax(spectrum[allowed])]])
        lo=max(config.min_frequency,(peak-1.1)/(window*1e-6))
        hi=min(config.max_frequency,(peak+1.1)/(window*1e-6))
        if hi<=lo:
            return np.zeros(6,np.float32)
        fit=estimate_sweep(image,float(window),lo,hi)
        if not fit.valid:
            return np.zeros(6,np.float32)
        uncertainty=max(150.,fit.uncertainty_hz,2*fit.frequency/max(1,bottom-top))
        estimates.append((fit.frequency,uncertainty,fit.residual,fit.cycles,fit.confidence))
    if len(estimates)!=2 or abs(estimates[0][0]-estimates[1][0])>2*max(e[1] for e in estimates):
        return np.zeros(6,np.float32)
    return np.array([1,np.mean([e[0] for e in estimates]),max(e[1] for e in estimates),
                     max(e[2] for e in estimates),np.mean([e[3] for e in estimates]),
                     min(e[4] for e in estimates)],np.float32)


def fit_support(fits,mask,frequency):
    rows=np.asarray(fits)[np.asarray(mask,dtype=bool)]
    rows=rows[rows[:,0]>.5]
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            if abs(a[1]-b[1])<=2*max(a[2],b[2]) and all(abs(r[1]-frequency)<=2*r[2] for r in (a,b)):
                return True
    return False


class HybridNet(nn.Module):
    def __init__(self,classes,config,continuous_frozen=False):
        super().__init__()
        self.single=MultiNet(classes)
        self.continuous=MultiNet(classes)
        self.router=nn.Sequential(nn.Linear(256,96),nn.GELU(),nn.Linear(96,2))
        self.config=config
        self.continuous_frozen=continuous_frozen
        self.continuous.requires_grad_(not continuous_frozen)

    def encode(self,texture,spectrum,meta):
        a=self.single.encode(texture,spectrum,meta)
        if self.continuous_frozen:
            with torch.no_grad():
                b=self.continuous.encode(texture,spectrum,meta)
        else:
            b=self.continuous.encode(texture,spectrum,meta)
        return torch.cat((a,b),dim=-1)

    def continuous_mask(self,mask):
        available=torch.tensor([t in self.config.continuous_windows for t in self.config.windows],device=mask.device)
        return mask&available[None]

    def contexts(self,encoded,mask):
        a=self.single.context(encoded[...,:128],mask)
        cmask=self.continuous_mask(mask)
        valid=cmask.any(1)
        b=torch.zeros_like(a)
        if valid.any():
            b[valid]=self.continuous.context(encoded[valid,:,128:],cmask[valid])
        return a,b,valid

    def router_logits(self,encoded,mask):
        a,b,_=self.contexts(encoded,mask)
        # Mode supervision must not distort the frequency experts' features.
        return self.router(torch.cat((a.detach(),b.detach()),dim=1))

    def diagnostics(self,encoded,mask):
        a,b,valid=self.contexts(encoded,mask)
        pa=self.single.classify(a).softmax(1)
        pb=self.continuous.classify(b).softmax(1)
        pb=torch.where(valid[:,None],pb,torch.full_like(pb,1/pb.shape[1]))
        route=self.router(torch.cat((a,b),1)).softmax(1)
        return pa,pb,route

    def fuse(self,encoded,mask):
        pa,pb,route=self.diagnostics(encoded,mask)
        p=route[:,:1]*pa+route[:,1:]*pb
        grid=torch.linspace(0,1,p.shape[1],device=p.device)
        return p.clamp_min(1e-12).log(),(p*grid).sum(1)

    def choose_scores(self,encoded,mask,meta):
        sa=self.single.choose_scores(encoded[...,:128],mask,meta).softmax(1)
        cmask=self.continuous_mask(mask)
        cb=torch.zeros_like(sa)
        valid=cmask.any(1)
        if valid.any():
            scores=self.continuous.choose_scores(encoded[valid,:,128:],cmask[valid],meta)
            available=torch.tensor([t in self.config.continuous_windows for t in self.config.windows],device=mask.device)
            scores=scores.masked_fill(~available[None]|mask[valid],-1e9)
            has_choice=((~mask[valid])&available[None]).any(1)
            cb[valid]=scores.softmax(1)*has_choice[:,None]
        route=self.router_logits(encoded,mask).softmax(1)
        return (route[:,:1]*sa+route[:,1:]*cb).clamp_min(1e-12).log().masked_fill(mask,-1e9)


def training_loss(model,encoded,mask,meta,labels,modes):
    loss=encoded.sum()*0
    selection=encoded.sum()*0
    for mode,expert,part in ((0,model.single,encoded[...,:128]),(1,model.continuous,encoded[...,128:])):
        use=modes==mode
        if not use.any() or (mode==1 and model.continuous_frozen):
            continue
        ids=list(range(len(model.config.windows))) if mode==0 else [j for j,t in enumerate(model.config.windows) if t in model.config.continuous_windows]
        expert_mask=mask[use][:,ids]
        # A random mask containing only the new single-only window cannot
        # supervise the continuous expert; sample its initial probe as well.
        expert_mask=expert_mask.clone()
        expert_mask[~expert_mask.any(1),ids.index(model.config.windows.index(175.))]=True
        expert_loss,sel=train_losses(expert,part[use][:,ids],expert_mask,meta[ids],labels[use])
        loss=loss+expert_loss; selection=selection+sel
    router_loss=nn.functional.cross_entropy(model.router_logits(encoded,mask),modes)
    router_loss=router_loss+.5*nn.functional.cross_entropy(model.router_logits(encoded,torch.ones_like(mask)),modes)
    return loss+router_loss,selection,router_loss


def observation_stage(model,encoded,mask,meta,fits,config,temperature=1.):
    """One inference step from observed embeddings + observed geometric fits."""
    logits,_=model.fuse(encoded,mask)
    p=(logits/temperature).softmax(1)
    pa,pb,route=model.diagnostics(encoded,mask)
    scores=model.choose_scores(encoded,mask,meta).detach().clone()
    probabilities=p.detach().cpu().numpy(); modes=route.detach().cpu().numpy()
    stages=[]
    known=mask.detach().cpu().numpy()
    fits=np.asarray(fits)
    for i,prob in enumerate(probabilities):
        frequency=int(config.frequencies[int(prob.argmax())])
        supported=fit_support(fits[i],known[i],frequency)
        single_prob=float(modes[i,0])
        if supported:
            route_name='single_curve_crosscheck'
            eligible=True
        elif single_prob>=.8:
            route_name='single_needs_crosscheck'; eligible=False
        elif single_prob<=.2:
            route_name='continuous_expert'; eligible=True
        else:
            route_name='ambiguous_mixture'
            eligible=int(pa[i].argmax())==int(pb[i].argmax()) and int(mask[i].sum())>=3
        # Only observed fits may suggest a better single-scan time scale.
        valid=fits[i][known[i]&(fits[i,:,0]>.5)]
        if single_prob>=.8 and len(valid) and not supported:
            f=float(np.median(valid[:,1]))
            quality=np.array([-abs(math.log(max(1e-5,f*t*1e-6)/5)) for t in config.windows])
            hint=torch.tensor(quality,device=scores.device,dtype=scores.dtype)
            scores[i]=scores[i]+.7*hint
        stages.append({'prediction':int(prob.argmax()),'confidence':float(prob.max()),
                       'eligible':bool(eligible),'single_probability':single_prob,
                       'route':route_name,'fit_supported':supported})
    return probabilities,stages,scores.masked_fill(mask,-1e9)


def accepted(path,calibration):
    if not calibration or calibration.get('threshold',1.01)>1:
        return None
    for j,step in enumerate(path):
        if j and step['eligible'] and step['prediction']==path[j-1]['prediction'] and step['confidence']>=calibration['threshold']:
            return j
    return None


class HybridRecognizer:
    """Unified API, intentionally does not accept acquisition-mode truth."""
    def __init__(self,model,config,calibration=None):
        self.model,self.config,self.calibration=model.eval(),config,calibration
        self.device=next(model.parameters()).device
        self.meta=torch.tensor(metadata(config),device=self.device)
        self.encoded=torch.zeros(1,len(config.windows),256,device=self.device)
        self.mask=torch.zeros(1,len(config.windows),dtype=torch.bool,device=self.device)
        self.fits=np.zeros((1,len(config.windows),6),np.float32)
        self.used,self.path=[],[]
        self.probability=np.full(len(config.frequencies),1/len(config.frequencies))
        self.scores=None

    def observe(self,window_us,images):
        if window_us not in self.config.windows or window_us in self.used:
            raise ValueError('Unknown or repeated window')
        j=self.config.windows.index(window_us)
        x,s=encode_pair(images)
        self.fits[0,j]=fit_single_pair(images,window_us,self.config)
        with torch.inference_mode():
            z=self.model.encode(torch.tensor(x[None,None],device=self.device).float()/255,
                torch.tensor(s[None,None],device=self.device),self.meta[j:j+1])
            self.encoded[:,j]=z[:,0]; self.mask[:,j]=True
            p,stages,self.scores=observation_stage(self.model,self.encoded,self.mask,self.meta,
                self.fits,self.config,(self.calibration or {}).get('temperature',1.))
        self.probability=p[0]; self.path.append(stages[0]); self.used.append(float(window_us))

    def decide(self):
        confirmed=accepted(self.path[-2:],self.calibration) is not None
        next_window=None
        if not confirmed:
            for initial in (175.,1750.):
                if initial not in self.used:
                    next_window=initial; break
            else:
                if len(self.used)<len(self.config.windows):
                    next_window=self.config.windows[int(self.scores[0].argmax())]
        result=frequency_result(self.config,self.probability,self.used,self.calibration,confirmed,next_window)
        result.update(source='hybrid_frequency',route=self.path[-1]['route'] if self.path else 'unobserved',
                      single_probability=self.path[-1]['single_probability'] if self.path else None,
                      geometric_support=self.path[-1]['fit_supported'] if self.path else False,
                      valid_fit_windows=[self.config.windows[j] for j in range(len(self.config.windows)) if self.fits[0,j,0]>.5])
        return result
