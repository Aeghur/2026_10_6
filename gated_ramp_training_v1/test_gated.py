"""Bounded verification only. Optimizer updates are disabled in workflow tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import numpy as np
import torch
from PIL import Image
from gated_frequency import Config,Scan,Scene,experiment,baseline,extract,Ranker,profile
from train_gated import Session,handler

torch.set_num_threads(2)


class GatedTests(unittest.TestCase):
    def test_timing_and_relative_clock(self):
        c=Config(clock_ppm=0)
        p=Scene(90100,c,2)
        self.assertTrue(np.array_equal(np.asarray(p.render(Scan(50,0,0)))>75,np.asarray(p.render(Scan(50,0,100)))>75))
        # A fractional active cycle is fine; fixed repetition is the important part.
        self.assertAlmostEqual(90100*50e-6,4.505)
        with self.assertRaises(ValueError):Scan(4000,7000).validate()
        with self.assertRaises(ValueError):Config(clock_ppm=float('nan')).validate()

    def test_image_only_frequency_cases(self):
        c=Config()
        for f in [1000,3000,26000,81000,90100,100000]:
            x,meta,ims=experiment(c,f,1,preview=True)
            self.assertEqual(int(c.grid[baseline(x).argmin()]),f)
            self.assertEqual(len(ims),6)
            self.assertTrue(all(s.active_us+s.delay_us<=10000 for im,s in ims))
            # Repeating inference requires no simulator object or source label.
            np.testing.assert_array_equal(x,extract(ims[3:],c.grid,c.clock_ppm))

    def test_endpoint_mask_and_blank(self):
        c=Config();p=Scene(81000,c,12);im=p.render(Scan(50))
        before=profile(im)[0]
        a=np.asarray(im).copy();a[34:42]=255
        np.testing.assert_array_equal(before,profile(Image.fromarray(a))[0])
        x=extract([(Image.new('L',(384,384)),Scan(50))],c.grid,c.clock_ppm)
        self.assertGreater(float(baseline(x).min()),.1)

    def test_workflow_resume_safe_load_and_frozen_evaluation(self):
        c=Config(train_cases=4,validation_cases=4,test_cases=4,epochs=1)
        with tempfile.TemporaryDirectory() as tmp:
            s=Session(tmp,c)
            initial=Ranker()
            # Exercise gradients, loss, saving and resume; no optimizer update.
            with patch.object(torch.optim.Adam,'step',return_value=None) as step:
                s.start(c,'train');s.worker.join(30)
                self.assertFalse(s.worker.is_alive());self.assertNotEqual(s.state['state'],'error',s.state)
                self.assertTrue(step.called)
            path=Path(tmp)/'best.pt';self.assertTrue(path.exists())
            saved=torch.load(path,weights_only=True)
            resumed=Session(tmp,c);model,_=resumed.load()
            self.assertEqual(resumed.epoch,1)
            for k,v in model.state_dict().items():self.assertTrue(torch.equal(v,saved['model'][k]))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            resumed.start(c,'evaluate');resumed.worker.join(30)
            self.assertNotEqual(resumed.state['state'],'error',resumed.state)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
            result=json.loads((Path(tmp)/'test_result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['count'],4)
            with self.assertRaises(ValueError):resumed.start(Config(clock_ppm=20),'train')

    def test_stop_before_dataset_writes_safe_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Session(tmp,Config(train_cases=2,validation_cases=2))
            s.event.set();s.work('train')
            self.assertEqual(s.state['state'],'stopped')
            cp=torch.load(Path(tmp)/'latest.pt',weights_only=True)
            self.assertEqual(cp['cursor'],0)

    def test_http_idle_and_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Session(tmp,Config());server=ThreadingHTTPServer(('127.0.0.1',0),handler(s))
            t=threading.Thread(target=server.serve_forever);t.start()
            root=f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertIn('门控扫描'.encode(),urlopen(root).read())
                state=json.load(urlopen(root+'/api/state'))
                self.assertEqual(state['state'],'idle');self.assertIsNone(s.worker)
                req=Request(root+'/api/train',data=b'{}',headers={'Origin':'https://external.example'})
                with self.assertRaises(HTTPError) as e:urlopen(req)
                self.assertEqual(e.exception.code,403)
                self.assertEqual(list(Path(tmp).iterdir()),[])
            finally:server.shutdown();server.server_close();t.join()


if __name__=='__main__':unittest.main()
