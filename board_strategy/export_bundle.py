"""Export current best weights and a self-contained board handoff zip. No training."""
import argparse
import ast
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LAB = ROOT/'lissajous_lab'
VENDOR = ('hybrid_frequency', 'multisweep_model', 'overlap_model', 'sweep_optics',
          'active_optics', 'sweep_vision', 'pixel_vision', 'phase_probe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=LAB/'hybrid_runs'/'mixed_v3'/'best.pt')
    parser.add_argument('--output', type=Path, default=ROOT/'deliverables'/'lissajous_board_strategy_v1.zip')
    args = parser.parse_args()
    sys.path.insert(0, str(LAB))
    import torch
    from hybrid_frequency import HybridConfig, VERSION
    source = args.checkpoint.resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    saved = torch.load(source, map_location='cpu', weights_only=True)
    if saved['version'] != VERSION or saved.get('calibration') is None:
        raise ValueError('Export a calibrated hybrid best.pt; a partial latest.pt may have no calibration')
    config = HybridConfig(**saved['config']).validate()
    folder = HERE/'lissajous_control'
    vendor = folder/'_vendor'; vendor.mkdir(exist_ok=True)
    (vendor/'__init__.py').write_text('"""Snapshot of the measured V3 inference dependencies."""\n', encoding='utf-8')
    source_hashes = {}
    for name in VENDOR:
        original = (LAB/(name+'.py')).read_text(encoding='utf-8')
        source_hashes[name] = hashlib.sha256(original.encode('utf-8')).hexdigest()
        # Preserve code exactly except relative imports for a real Python
        # package. No sys.path mutation inside the delivered library.
        tree = ast.parse(original)
        lines = original.splitlines(keepends=True)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in VENDOR:
                line = lines[node.lineno-1]
                lines[node.lineno-1] = line.replace('from '+node.module+' import', 'from .'+node.module+' import', 1)
        (vendor/(name+'.py')).write_text(''.join(lines), encoding='utf-8')
    assets = folder/'assets'; assets.mkdir(exist_ok=True)
    artifact = {'format': 'lissajous-board-frequency-1', 'config': asdict(config),
                'model': saved['model'], 'calibration': saved['calibration']}
    temporary = assets/'frequency.tmp'; torch.save(artifact, temporary); temporary.replace(assets/'frequency.pt')
    manifest = {'format': artifact['format'], 'sha256': hashlib.sha256((assets/'frequency.pt').read_bytes()).hexdigest(),
        'source_checkpoint_name': source.name, 'source_sha256': source_hash,
        'source_epoch': saved['epoch'], 'source_updates': saved['updates'],
        'frequency_grid_hz': [config.min_frequency, config.max_frequency, 100],
        'windows_us': list(config.windows), 'canvas_pixels': [384,384], 'canvas_div': [-5,-5,5,5],
        'ramp_reset_to_rise_ratio': .05, 'training_exposure_ms': config.exposure_ms,
        'training_persistence_ms': config.persistence_ms, 'learned_scope': 'frequency_only',
        'hardware_validation': 'not_performed', 'inference_source_hashes': source_hashes}
    (assets/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('Checkpoint changed during export')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(HERE.rglob('*')):
            if not path.is_file() or any(part in ('.git', '__pycache__', '.pytest_cache', '.venv', 'build', 'dist') or part.endswith('.egg-info') for part in path.relative_to(HERE).parts):
                continue
            if path.suffix in ('.pyc', '.tmp'):
                continue
            if path.name == 'export_bundle.py':
                continue  # Source-workspace exporter, not a board dependency.
            archive.write(path, Path('lissajous_board_strategy')/path.relative_to(HERE))
    print(json.dumps({'zip': str(args.output.resolve()), 'source_epoch': saved['epoch'],
                      'weights_sha256': manifest['sha256'], 'bytes': args.output.stat().st_size}))


if __name__ == '__main__':
    main()
