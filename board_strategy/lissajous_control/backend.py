"""Portable, inference-only loading. No optimizer, calibration or training."""
import hashlib
import json
from pathlib import Path
import torch
from ._vendor.hybrid_frequency import HybridConfig, HybridNet, HybridRecognizer


class FrequencyBackend:
    def __init__(self, artifact_dir=None, threads=2):
        root = Path(artifact_dir) if artifact_dir else Path(__file__).parent/'assets'
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        path = root/'frequency.pt'
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
            raise ValueError('Frequency artifact checksum mismatch')
        if not 1 <= threads <= 16:
            raise ValueError('threads must be 1..16')
        torch.set_num_threads(threads)
        saved = torch.load(path, map_location='cpu', weights_only=True)
        if saved['format'] != 'lissajous-board-frequency-1' or not saved['calibration']:
            raise ValueError('Not a calibrated deployment artifact')
        self.config = HybridConfig(**saved['config']).validate()
        self.model = HybridNet(len(self.config.frequencies), self.config, True)
        self.model.load_state_dict(saved['model']); self.model.eval(); self.model.requires_grad_(False)
        self.calibration = saved['calibration']
        self.manifest = manifest
        self.reset()

    def reset(self):
        self.observer = HybridRecognizer(self.model, self.config, self.calibration)

    def next_window(self):
        return self.observer.decide()['next_window_us']

    def observe(self, window, images):
        with torch.inference_mode():
            self.observer.observe(window, images)
            return self.observer.decide()
