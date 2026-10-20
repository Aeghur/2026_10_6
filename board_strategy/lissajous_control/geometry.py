"""Fixed per-measurement image mapping; no automatic geometry redetection."""
from dataclasses import dataclass
import json
from pathlib import Path
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Calibration:
    session_id: str
    image_size_wh: tuple
    image_to_div_homography: tuple

    def __post_init__(self):
        h = np.asarray(self.image_to_div_homography, float)
        if not self.session_id or len(self.image_size_wh) != 2 or min(self.image_size_wh) < 32:
            raise ValueError('Calibration requires session ID and raw image dimensions')
        if h.shape != (3, 3) or not np.isfinite(h).all() or abs(np.linalg.det(h)) < 1e-12:
            raise ValueError('Invalid raw-pixel -> div homography')

    @classmethod
    def from_session_reference(cls, path):
        record = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        session = record.get('measurement_session', {})
        if not session.get('id') or not session.get('image_size_wh'):
            raise ValueError('Use a NEW measurement_session reference.json, not an old provisional circle fit')
        return cls(session['id'], tuple(session['image_size_wh']), tuple(map(tuple, record['image_to_div_homography'])))


def prepare_image(image, calibration=None, *, channel_order='RGB', threshold=None, valid_mask=None):
    """Output the model's 384x384, 10x10-div canvas. div Y in H is DOWN.

    Grid/UI/border suppression belongs here or in the caller. A threshold is
    optional because a camera mask and a simulated antialiased bitmap differ.
    Never resize an 8x8-div crop to 384x384; that changes the trained scale.
    """
    if channel_order not in ('RGB', 'BGR'):
        raise ValueError('channel_order must be RGB or BGR')
    if isinstance(image, Image.Image):
        im = image.copy()
    else:
        pixels = np.asarray(image)
        if pixels.dtype != np.uint8 or pixels.ndim not in (2, 3):
            raise ValueError('Expected uint8 grayscale/RGB/BGR image')
        if pixels.ndim == 3 and channel_order == 'BGR':
            pixels = pixels[..., :3][..., ::-1]
        im = Image.fromarray(pixels)
    if calibration is not None:
        if im.size != tuple(calibration.image_size_wh):
            raise ValueError('Camera dimensions changed; start a new calibration')
        canvas_to_div = np.array([[10/383, 0, -5], [0, 10/383, -5], [0, 0, 1.]])
        inverse = np.linalg.inv(np.asarray(calibration.image_to_div_homography)) @ canvas_to_div
        inverse /= inverse[2, 2]
        im = im.transform((384, 384), Image.Transform.PERSPECTIVE, tuple(inverse.ravel()[:8]),
                          resample=Image.Resampling.BILINEAR)
    elif im.size != (384, 384):
        raise ValueError('Supply raw-camera calibration or an already calibrated 384x384 image')
    im = im.convert('L')
    pixels = np.array(im)
    if threshold is not None:
        if not 1 <= threshold <= 254:
            raise ValueError('threshold must be 1..254')
        pixels[pixels < threshold] = 0
    if valid_mask is not None:
        mask = np.asarray(valid_mask)
        if mask.shape != (384, 384):
            raise ValueError('valid_mask must already be in canonical 384x384 coordinates')
        pixels[mask == 0] = 0
    return Image.fromarray(pixels)
