"""Supervised multiframe frequency recognition; inference sees finite pixels only.

The old continuous/max-union renderer is deliberately retained. Nothing here
assumes a single trace or rejects dense images before trying recognition.
"""
from dataclasses import asdict, dataclass
import math

import numpy as np
from PIL import Image
import torch
from torch import nn

from .sweep_optics import SweepPlant, SweepSetting

VERSION = 'overlap-frequency-1'
WINDOWS = (175., 1750.)
SPLITS = {'train': 1103, 'validation': 2207, 'test': 3301, 'audit': 4409}


@dataclass
class OverlapConfig:
    min_frequency: int = 1000
    max_frequency: int = 100000
    seed: int = 20261018
    domain: str = 'clean'
    exposure_ms: float = 8.
    persistence_ms: float = 2.
    train_per_frequency: int = 4
    validation_cases: int = 256
    test_cases: int = 512
    epochs: int = 20
    batch_size: int = 16
    learning_rate: float = .001

    def validate(self):
        bounds = {'min_frequency': (1000, 100000), 'max_frequency': (1000, 100000),
                  'seed': (0, 2**31-1), 'train_per_frequency': (1, 100),
                  'validation_cases': (8, 4096), 'test_cases': (8, 4096),
                  'epochs': (1, 10000), 'batch_size': (1, 128)}
        for key, (lo, hi) in bounds.items():
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f'{key}: {lo}..{hi}, integer required')
        if self.min_frequency % 100 or self.max_frequency % 100 or self.max_frequency-self.min_frequency < 900:
            raise ValueError('Frequency range: 100 Hz grid, at least 10 classes')
        if self.domain not in ('clean', 'physical'):
            raise ValueError('domain must be clean or physical')
        for key, lo, hi in (('exposure_ms', .1, 40), ('persistence_ms', 0, 40),
                            ('learning_rate', .000001, .1)):
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f'{key}: {lo}..{hi}')
        return self

    @property
    def frequencies(self):
        return np.arange(self.min_frequency, self.max_frequency+1, 100, dtype=np.int64)


# Resume restores data/optics conditions. Only runtime controls may be changed.
RUNTIME = {'epochs', 'batch_size', 'learning_rate'}


def resolve_config(requested, saved):
    return OverlapConfig(**{k: (v if k in RUNTIME else saved[k])
                            for k, v in asdict(requested).items()}).validate()


def encode_images(images):
    """Four images -> low-resolution texture plus full-resolution spatial spectra.

    No labels, plant, source phase, or absolute simulation time are accepted.
    Full-resolution spectra preserve detail lost in CNN thumbnail resizing.
    """
    if len(images) != 4 or any(im.size != (384, 384) for im in images):
        raise ValueError('Expected four rectified 384x384 grayscale images')
    thumbs, spectra = [], []
    for image in images:
        gray = image.convert('L')
        thumbs.append(np.asarray(gray.resize((128, 128), Image.Resampling.BOX), np.uint8))
        pixels = np.asarray(gray, np.float32)[38:345, 38:345]/255.
        centered = pixels-pixels.mean(axis=0, keepdims=True)
        window = np.hanning(len(centered))[:, None]
        power = np.abs(np.fft.rfft(centered*window, n=512, axis=0))**2
        # Left/centre/right spectral power and the row brightness spectrum.
        parts = [power[:, a:b].mean(axis=1) for a, b in ((0, 102), (102, 205), (205, 307))]
        profile = pixels.mean(axis=1)
        parts.append(np.abs(np.fft.rfft((profile-profile.mean())*window[:, 0], n=512))**2)
        # Preserve absolute strength; per-image normalization would amplify noise
        # in saturated/blank frames and hide the lack of usable signal.
        spectra.extend([np.log1p(p).astype(np.float32)/8 for p in parts])
    return np.stack(thumbs), np.stack(spectra)


def capture_scene(config, frequency, scene_seed, check=lambda: None):
    """Label is used in the simulator only; features cross an image-only boundary."""
    rng = np.random.default_rng(scene_seed)
    plant = SweepPlant(int(frequency), float(rng.uniform(0, 360)),
                       seed=int(rng.integers(0, 2**31-1)), domain=config.domain,
                       size=384, acquisition='free', exposure_ms=config.exposure_ms,
                       persistence_ms=config.persistence_ms)
    # Acquisition schedule is independent of source frequency and phase.
    plant.advance(float(rng.uniform(.02, .3)))
    images = []
    for window in WINDOWS:
        check()
        frame = plant.command(SweepSetting(window))
        for repeat in range(2):
            check()
            if repeat:
                # Avoid a synthetic exact 30 Hz / 100 Hz-grid timing shortcut.
                plant.advance(float(rng.uniform(1/30-.004, 1/30+.004)))
                frame = plant.capture()
            attempts = 0
            while not frame.fresh:
                attempts += 1
                if attempts > 20:
                    raise RuntimeError('Camera returned too many stale frames')
                check()
                plant.advance(1/30)
                frame = plant.capture()
            images.append(frame.image.copy())
    texture, spectrum = encode_images(images)
    return texture, spectrum, images


def scene_plan(config, split):
    """All allowed classes train; held-out scenes use disjoint phase/noise seeds.

    This tests new observations of known frequency classes, NOT unseen classes.
    Withholding an output class entirely would make a classifier's accuracy on
    that class meaningless. The frequency grid itself is task knowledge.
    """
    rng = np.random.default_rng(np.random.SeedSequence([config.seed, SPLITS[split]]))
    n = len(config.frequencies)
    count = n*config.train_per_frequency if split == 'train' else getattr(config, split+'_cases')
    labels = np.concatenate([rng.permutation(n) for _ in range(math.ceil(count/n))])[:count]
    for index, label in enumerate(labels):
        seed = np.random.SeedSequence([config.seed, SPLITS[split], index]).generate_state(4).tolist()
        yield int(label), seed


class OverlapNet(nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.texture = nn.Sequential(
            nn.Conv2d(4, 16, 5, stride=2, padding=2), nn.GELU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.GELU(),
            nn.Conv2d(32, 48, 3, stride=2, padding=1), nn.GELU(),
            nn.AdaptiveAvgPool2d((4, 4)), nn.Flatten(), nn.Linear(768, 128), nn.GELU())
        self.spectrum = nn.Sequential(
            nn.Conv1d(16, 32, 5, padding=2), nn.GELU(),
            nn.Conv1d(32, 48, 5, stride=2, padding=2), nn.GELU(),
            nn.AdaptiveAvgPool1d(24), nn.Flatten(), nn.Linear(1152, 128), nn.GELU())
        self.shared = nn.Sequential(nn.Linear(256, 256), nn.GELU())
        self.classify = nn.Linear(256, classes)
        # Neighboring 100 Hz classes share a learned 1 kHz group prior.
        # This makes coarse localization learnable before exact-bin separation.
        self.coarse = nn.Linear(256, math.ceil(classes/10))
        self.regress = nn.Linear(256, 1)

    def forward(self, texture, spectrum):
        z = self.shared(torch.cat((self.texture(texture), self.spectrum(spectrum)), dim=1))
        fine = self.classify(z)
        logits = fine+self.coarse(z).repeat_interleave(10, dim=1)[:, :fine.shape[1]]
        return logits, torch.sigmoid(self.regress(z)).squeeze(1)


def summarize(probabilities, labels, frequencies):
    p = np.asarray(probabilities, np.float64)
    labels = np.asarray(labels, np.int64)
    order = np.argsort(-p, axis=1)
    truth = frequencies[labels]
    prediction = frequencies[order[:, 0]]
    errors = np.abs(prediction-truth)
    k = min(10, len(frequencies))
    correct = order[:, 0] == labels
    entropy = -(p*np.log(np.maximum(p, 1e-12))).sum(axis=1)
    return {'count': len(labels), 'top1': float(correct.mean()),
            'top5': float(np.any(order[:, :min(5, len(frequencies))] == labels[:, None], axis=1).mean()),
            'top10': float(np.any(order[:, :k] == labels[:, None], axis=1).mean()),
            'within_1500_hz': float((errors <= 1500).mean()),
            'mae_hz': float(errors.mean()), 'p90_error_hz': float(np.quantile(errors, .9)),
            'confident_wrong': float(((p.max(axis=1) >= .8) & ~correct).mean()),
            'entropy': float(entropy.mean()), 'chance_top1': 1/len(frequencies),
            'chance_top10': k/len(frequencies)}


def predict(model, images, frequencies):
    """Portable inference entry: four pixels-only frames and a public grid."""
    model.eval()
    texture, spectrum = encode_images(images)
    device = next(model.parameters()).device
    with torch.inference_mode():
        logits, _ = model(torch.from_numpy(texture[None]).to(device).float()/255,
                          torch.from_numpy(spectrum[None]).to(device))
        p = logits.softmax(1)[0].cpu().numpy()
    order = np.argsort(-p)[:min(10, len(p))]
    return [{'frequency': int(frequencies[i]), 'probability': float(p[i])} for i in order]
