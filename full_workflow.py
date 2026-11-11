"""Fresh image-only frequency acquisition followed by optical lock v2."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', required=True)
    parser.add_argument('--mode', choices=('circle', 'diagonal', 'infinity'), default='circle',
                        help='最终目标：circle圆（默认）、diagonal正斜线、infinity输出二倍频图形')
    args = parser.parse_args()
    out = ROOT / 'artifacts/diagnostics' / f'full_workflow_{time.time_ns()}'
    out.mkdir(parents=True)
    report = dict(success=False, mode=args.mode, stages=[], ground_truth_used=False)
    began = time.monotonic()
    print('ARTIFACTS', out, flush=True)

    def run_stage(name, arguments):
        started = time.monotonic()
        log = out / f'{name}.log'
        artifacts = None
        with log.open('w') as stream:
            process = subprocess.Popen([sys.executable, '-u', *arguments], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                print(name.upper(), line.rstrip(), flush=True)
                if line.startswith('ARTIFACTS '):
                    artifacts = Path(line.removeprefix('ARTIFACTS ').strip())
                    if not artifacts.is_absolute():
                        artifacts = ROOT / artifacts
            code = process.wait()
        report['stages'].append(dict(name=name, exit_code=code,
            seconds=time.monotonic()-started, artifacts=str(artifacts), log=str(log)))
        if code or artifacts is None:
            if artifacts is not None and (artifacts / 'result.json').is_file():
                failure = json.loads((artifacts / 'result.json').read_text())
                if failure.get('output_state_verification_required'):
                    report.update(output_state=failure.get('output_state'),
                        output_state_verification_required=True, automatic_retry_performed=False,
                        final_output=None)
            raise RuntimeError(f'{name} failed; see {log}')
        return artifacts

    try:
        frequency_out = run_stage('frequency', ['experiments/gated_model/two_flush_two_stage.py', '--hardware'])
        frequency_report = json.loads((frequency_out / 'summary.json').read_text())
        refined = frequency_report.get('refinement', {})
        nominal = refined.get('frequency_hz')
        if refined.get('status') != 'frequency_candidate' or nominal is None:
            raise RuntimeError('No separated nominal frequency candidate; optical lock was not started')
        report['nominal_source_hz'] = nominal
        report['frequency_candidate'] = refined
        lock_out = run_stage('lock', ['lock_hardware_test.py', '--source-hz', str(nominal),
                                    '--mode', args.mode, '--initial-lead', '0'])
        locked = json.loads((lock_out / 'result.json').read_text())
        report['success'] = locked['success']
        report['final_status'] = locked.get('final_status')
        report['final_output'] = locked.get('final_output')
        report['sampling_summary'] = locked.get('sampling_summary')
        if args.mode == 'infinity':
            report['target_switch_completed'] = locked.get('target_switch_completed', False)
            report['final_output_stage'] = locked.get('final_output_stage')
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['total_seconds'] = time.monotonic()-began
        (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('RESULT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
