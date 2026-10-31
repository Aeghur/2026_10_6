"""Trained two-head MLP, evaluated with NumPy. No Torch dependency."""
import hashlib,json
from pathlib import Path
import numpy as np


class Policy:
    def __init__(self,assets=None):
        assets=Path(assets) if assets else Path(__file__).with_name('assets')
        self.metadata=json.loads((assets/'policy.json').read_text(encoding='utf-8'))
        data=(assets/'policy.npz').read_bytes()
        if hashlib.sha256(data).hexdigest()!=self.metadata['numpy_weights_sha256']:raise ValueError('Policy checksum differs')
        with np.load(assets/'policy.npz',allow_pickle=False) as f:self.weights={k:f[k] for k in f.files}
        for head in ('frequency','phase'):
            for suffix,shape in [('0_weight',(48,22)),('0_bias',(48,)),('2_weight',(3,48)),('2_bias',(3,))]:
                a=self.weights.get(head+'_'+suffix)
                if a is None or a.shape!=shape or not np.isfinite(a).all():raise ValueError('Invalid policy array')

    def scores(self,context,head):
        if head not in ('frequency','phase'):raise ValueError('Unknown policy head')
        x=np.asarray(context,dtype=np.float32)
        if x.shape!=(22,) or not np.isfinite(x).all():raise ValueError('Expected 22 finite image/public features')
        w=self.weights
        z=np.tanh(w[head+'_0_weight']@x+w[head+'_0_bias'])
        return w[head+'_2_weight']@z+w[head+'_2_bias']

    def choose(self,context,head):return int(self.scores(context,head).argmax())
