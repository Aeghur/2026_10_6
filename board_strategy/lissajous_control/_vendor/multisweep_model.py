"""V2 asynchronous multiwindow recognition and a pixel-only sequential policy.

The source labels are not accepted by any inference/controller entry point.
Unselected windows are masked before fusion and next-action selection.
"""
from dataclasses import dataclass, asdict
import math
import numpy as np
from PIL import Image
import torch
from torch import nn

from .overlap_model import OverlapConfig, encode_images
from .sweep_optics import SweepPlant, SweepSetting

VERSION = 'multisweep-active-2.0'
SPLIT_IDS = {'train': 51011, 'validation': 52021, 'calibration': 53033, 'test': 54047, 'audit': 55051}
RUNTIME = {'epochs', 'batch_size', 'learning_rate'}


@dataclass
class MultiConfig(OverlapConfig):
    windows_us: str = '73,110,175,223,809,1750'
    train_per_frequency: int = 8
    batch_size: int = 8
    calibration_cases: int = 192
    target_error: float = .02
    min_calibration_accepts: int = 32

    @property
    def windows(self):
        try:
            return tuple(float(v.strip()) for v in self.windows_us.split(','))
        except (ValueError, AttributeError):
            raise ValueError('windows_us must be comma-separated microseconds') from None

    def validate(self):
        super().validate()
        windows = self.windows
        if not 3 <= len(windows) <= 10 or len(set(windows)) != len(windows):
            raise ValueError('Use 3..10 distinct windows')
        if any(not math.isfinite(v) or not 25 <= v <= 5000 for v in windows):
            raise ValueError('Ramp ascending windows must be 25..5000 us')
        if not {175., 1750.}.issubset(windows):
            raise ValueError('Keep 175 and 1750 us as the initial baseline pair')
        for key, lo, hi in (('calibration_cases', 32, 4096), ('min_calibration_accepts', 8, 4096)):
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f'{key}: integer {lo}..{hi}')
        if self.min_calibration_accepts > self.calibration_cases:
            raise ValueError('min_calibration_accepts exceeds calibration_cases')
        if isinstance(self.target_error, bool) or not isinstance(self.target_error, (float, int)) or not math.isfinite(self.target_error) or not 0 <= self.target_error <= .2:
            raise ValueError('target_error: 0..0.2')
        return self


def restore_config(requested, saved):
    return MultiConfig(**{k: v if k in RUNTIME else saved[k] for k, v in asdict(requested).items()}).validate()


class MultiPlant(SweepPlant):
    """Same max-union free-sweep renderer, with an explicitly wider command set."""
    def command(self, setting, wait=None):
        if not isinstance(setting, SweepSetting):
            return super().command(setting, wait)
        if not math.isfinite(setting.window_us) or not 25 <= setting.window_us <= 5000 or not .1 <= setting.vpp <= 5:
            raise ValueError('Unsupported ramp setting')
        self.setting = setting
        self.ramp_origin = self.time
        self.commands += 1
        self.advance(self.latency if wait is None else wait)
        return self.capture()


def make_plant(config, scene_seed, frequency):
    rng = np.random.default_rng(scene_seed)
    plant = MultiPlant(int(frequency), float(rng.uniform(0, 360)),
                       seed=int(rng.integers(0, 2**31-1)), domain=config.domain,
                       acquisition='free', exposure_ms=config.exposure_ms, persistence_ms=config.persistence_ms)
    plant.advance(float(rng.uniform(.02, .3)))
    return plant, rng


def capture_pair(plant, window_us, rng, check=lambda: None):
    images, captured = [], 0
    frame = plant.command(SweepSetting(float(window_us)))
    for repeat in range(2):
        check()
        if repeat:
            plant.advance(float(rng.uniform(1/30-.004, 1/30+.004)))
            frame = plant.capture()
        for attempt in range(21):
            captured += 1
            if frame.fresh:
                break
            check()
            plant.advance(1/30)
            frame = plant.capture()
        else:
            raise RuntimeError('Too many stale camera frames')
        images.append(frame.image.copy())
    return images, captured


def encode_pair(images):
    if len(images) != 2:
        raise ValueError('One window needs two fresh rectified 384x384 frames')
    _, spectrum = encode_images(images+images)
    texture = np.stack([np.asarray(image.convert('L').resize((96, 96), Image.Resampling.BOX), np.uint8) for image in images])
    return texture, spectrum[:8]


def metadata(config):
    return np.asarray([[math.log2(t/1750)/6, config.exposure_ms/40,
                        config.persistence_ms/40] for t in config.windows], np.float32)


def plan(config, split):
    rng = np.random.default_rng([config.seed, SPLIT_IDS[split]])
    n = len(config.frequencies)
    count = n*config.train_per_frequency if split == 'train' else getattr(config, split+'_cases')
    labels = np.concatenate([rng.permutation(n) for _ in range(math.ceil(count/n))])[:count]
    for i, label in enumerate(labels):
        yield int(label), np.random.SeedSequence([config.seed, SPLIT_IDS[split], i]).generate_state(4).tolist()


class MultiNet(nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.texture = nn.Sequential(nn.Conv2d(2, 12, 5, 2, 2), nn.GELU(),
            nn.Conv2d(12, 24, 3, 2, 1), nn.GELU(), nn.Conv2d(24, 32, 3, 2, 1), nn.GELU(),
            nn.AdaptiveAvgPool2d((3, 3)), nn.Flatten(), nn.Linear(288, 80), nn.GELU())
        self.spectrum = nn.Sequential(nn.Conv1d(8, 16, 5, padding=2), nn.GELU(),
            nn.Conv1d(16, 24, 5, 2, 2), nn.GELU(), nn.AdaptiveAvgPool1d(20),
            nn.Flatten(), nn.Linear(480, 96), nn.GELU())
        self.embed = nn.Sequential(nn.Linear(179, 128), nn.GELU())
        self.attend = nn.Linear(128, 1)
        self.fine = nn.Linear(128, classes)
        self.coarse = nn.Linear(128, math.ceil(classes/10))
        self.ordinal = nn.Linear(128, 1)
        self.select = nn.Sequential(nn.Linear(134, 64), nn.GELU(), nn.Linear(64, 1))

    def encode(self, texture, spectrum, meta):
        # [B,W,2,H,H], [B,W,8,257], [W,3] -> [B,W,128]
        b, w = texture.shape[:2]
        t = self.texture(texture.flatten(0, 1))
        s = self.spectrum(spectrum.flatten(0, 1))
        public = meta.unsqueeze(0).expand(b, -1, -1).reshape(b*w, 3)
        return self.embed(torch.cat((t, s, public), dim=1)).reshape(b, w, 128)

    def classify(self, context):
        fine = self.fine(context)
        return fine+self.coarse(context).repeat_interleave(10, dim=-1)[..., :fine.shape[-1]]

    def context(self, encoded, mask):
        if torch.any(~mask.any(dim=1)):
            raise ValueError('At least one window must be observed')
        weights = self.attend(encoded).squeeze(-1).masked_fill(~mask, -1e9).softmax(1)
        # Mask explicitly also in the product; not just in attention softmax.
        return (encoded*weights.unsqueeze(-1)*mask.unsqueeze(-1)).sum(1)

    def fuse(self, encoded, mask):
        context = self.context(encoded, mask)
        return self.classify(context), torch.sigmoid(self.ordinal(context)).squeeze(-1)

    def choose_scores(self, encoded, mask, meta):
        # Candidate input is only its known window duration. Its future image
        # embedding is never passed into the learned selector.
        context = self.context(encoded, mask)
        p = self.classify(context).softmax(1)
        grid = torch.linspace(0, 1, p.shape[-1], device=p.device)
        mean = (p*grid).sum(1)
        variance = (p*(grid-mean[:, None])**2).sum(1)
        entropy = -(p*p.clamp_min(1e-9).log()).sum(1)/math.log(p.shape[1])
        known = torch.cat((context, entropy[:, None], mean[:, None], variance[:, None]), dim=1)
        inputs = torch.cat((known[:, None].expand(-1, meta.shape[0], -1),
                            meta[None].expand(len(mask), -1, -1)), dim=-1)
        return self.select(inputs).squeeze(-1).masked_fill(mask, -1e9)


def train_losses(model, encoded, mask, meta, labels):
    logits, ordinal = model.fuse(encoded, mask)
    all_logits, _ = model.fuse(encoded, torch.ones_like(mask))
    individual = model.classify(encoded)
    loss = (nn.functional.cross_entropy(logits, labels, label_smoothing=.02)
            +.5*nn.functional.cross_entropy(all_logits, labels, label_smoothing=.02)
            +.2*nn.functional.cross_entropy(individual.flatten(0, 1), labels[:, None].expand(-1, encoded.shape[1]).flatten(), label_smoothing=.02)
            +.5*nn.functional.smooth_l1_loss(ordinal, labels.float()/max(1, logits.shape[1]-1)))
    eligible = ~mask.all(1)
    selection_loss = logits.sum()*0
    if eligible.any():
        # Privileged one-step teacher: training labels and counterfactual window
        # observations may teach the next action, but never enter inference.
        with torch.no_grad():
            gains = []
            current = logits.log_softmax(1).gather(1, labels[:, None]).squeeze(1)
            for j in range(mask.shape[1]):
                more = mask.clone(); more[:, j] = True
                after, _ = model.fuse(encoded.detach(), more)
                gains.append((after.log_softmax(1).gather(1, labels[:, None]).squeeze(1)-current).clamp(-6, 6))
            gains = torch.stack(gains, 1).masked_fill(mask, -1e9)
            teacher = (gains[eligible]/.5).softmax(1)
        scores = model.choose_scores(encoded, mask, meta)[eligible]
        selection_loss = -(teacher*scores.log_softmax(1)).sum(1).mean()
        loss = loss+.2*selection_loss
    return loss, selection_loss


def accepted(path, calibration, min_windows=2):
    if not calibration or calibration.get('threshold', 1.01) > 1:
        return None
    threshold = calibration['threshold']
    for i, step in enumerate(path):
        if i+1 >= min_windows and i > 0 and step['prediction'] == path[i-1]['prediction'] and step['confidence'] >= threshold:
            return i
    return None


def frequency_result(config, probability, used, calibration, confirmed, next_window=None, seconds=None, frames=None):
    order = np.argsort(-probability)[:min(10, len(probability))]
    return {'schema': 'frequency-estimate-1', 'source': 'continuous_multisweep',
            'frequency_hz': int(config.frequencies[order[0]]) if used else None, 'confirmed': bool(confirmed),
            'candidates': [{'frequency': int(config.frequencies[i]), 'probability': float(probability[i])} for i in order] if used else [],
            'windows_used_us': [float(v) for v in used], 'window_count': len(used),
            'next_window_us': next_window, 'acquisition_seconds': seconds, 'captured_frames': frames,
            'calibration': calibration or {'threshold': 1.01, 'reason': 'not_calibrated'},
            'note': '100 Hz-grid frequency recognition only; confirmation is empirically calibrated, not a guarantee.'}


class FrequencyRecognizer:
    """Public integration API: observe(window, [image,image]), then decide()."""
    def __init__(self, model, config, calibration=None):
        self.model, self.config, self.calibration = model.eval(), config, calibration
        self.device = next(model.parameters()).device
        self.meta = torch.tensor(metadata(config), device=self.device)
        self.encoded = torch.zeros(1, len(config.windows), 128, device=self.device)
        self.mask = torch.zeros(1, len(config.windows), dtype=torch.bool, device=self.device)
        self.path, self.used = [], []
        self.probability = np.full(len(config.frequencies), 1/len(config.frequencies))

    def observe(self, window_us, images):
        if window_us not in self.config.windows or window_us in self.used:
            raise ValueError('Unknown or already observed window')
        index = self.config.windows.index(window_us)
        texture, spectrum = encode_pair(images)
        with torch.inference_mode():
            z = self.model.encode(torch.tensor(texture[None, None], device=self.device).float()/255,
                torch.tensor(spectrum[None, None], device=self.device), self.meta[index:index+1])
            self.encoded[:, index] = z[:, 0]
            self.mask[:, index] = True
            logits, _ = self.model.fuse(self.encoded, self.mask)
            temperature = (self.calibration or {}).get('temperature', 1.)
            self.probability = (logits/temperature).softmax(1)[0].cpu().numpy()
        self.used.append(float(window_us))
        self.path.append({'prediction': int(self.probability.argmax()), 'confidence': float(self.probability.max())})

    def decide(self):
        # A previous high-confidence prefix cannot confirm a later conflicting
        # observation supplied by an external caller.
        confirmed = accepted(self.path[-2:], self.calibration) is not None
        next_window = None
        if not confirmed:
            for initial in (175., 1750.):
                if initial not in self.used:
                    next_window = initial
                    break
            else:
                if len(self.used) < len(self.config.windows):
                    with torch.inference_mode():
                        scores = self.model.choose_scores(self.encoded, self.mask, self.meta)
                    next_window = self.config.windows[int(scores[0].argmax())]
        return frequency_result(self.config, self.probability, self.used, self.calibration, confirmed, next_window)
