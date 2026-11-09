"""One persistent local JSON-lines process for C/C++/other-language callers."""
import argparse
import json
from pathlib import Path
import sys
from PIL import Image
from . import Controller, Target, Signal, Options, Capabilities, Frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', type=Path)
    args = parser.parse_args()
    controller = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            op = request['op']
            if op == 'start':
                if controller is not None and controller.stage not in ('idle', 'done', 'failed', 'stopped'):
                    raise ValueError('Stop the active measurement before replacing it')
                controller = Controller(request['calibration_id'],
                    Capabilities(**request.get('capabilities', {})), Options(**request.get('options', {})),
                    artifact_dir=args.artifact_dir)
                result = controller.start(Target(**request.get('target', {})), Signal(**request.get('signal', {})),
                                          now_s=request.get('now_s')).to_dict()
            elif controller is None:
                raise ValueError('start first')
            elif op == 'applied':
                controller.applied(request['command_id'], Signal(**request['signal']), request.get('applied_at_s'))
                result = {'status': 'acknowledged', 'capture_after_s': controller.capture_after}
            elif op == 'step':
                with Image.open(request['image_path']) as source:
                    # Canonical clean image only; raw images use prepare_image
                    # and the Python API or an upstream preprocessing process.
                    image = source.convert('L').copy()
                frame = Frame(image, Signal(**request['signal']), request['command_id'], request['frame_id'],
                              request['captured_at_s'], request['calibration_id'])
                result = controller.step(frame, now_s=request.get('now_s')).to_dict()
            elif op == 'stop':
                result = controller.stop().to_dict()
            else:
                raise ValueError('Unknown operation')
            print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
        except Exception as error:
            print(json.dumps({'status': 'error', 'reason': f'{type(error).__name__}: {error}'}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
