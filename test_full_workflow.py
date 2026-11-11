import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import full_workflow


class WorkflowFailureTests(unittest.TestCase):
    def test_uncertain_double_state_stops_workflow_and_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            class Process:
                def __init__(self, command, **kwargs):
                    calls.append(command)
                    self.failed = len(calls) == 2
                    stage = root / ('lock' if self.failed else 'frequency')
                    stage.mkdir()
                    if self.failed:
                        result = dict(success=False, output_state='unknown_after_arm_double_failure',
                                      output_state_verification_required=True)
                        (stage / 'result.json').write_text(json.dumps(result))
                    else:
                        result = dict(refinement=dict(status='frequency_candidate', frequency_hz=35300))
                        (stage / 'summary.json').write_text(json.dumps(result))
                    self.stdout = io.StringIO(f'ARTIFACTS {stage}\n')
                def wait(self): return 1 if self.failed else 0
            with patch.object(full_workflow, 'ROOT', root), \
                 patch.object(full_workflow.subprocess, 'Popen', Process), \
                 patch('sys.argv', ['full_workflow.py', '--hardware', '--mode', 'infinity']), \
                 patch('sys.stdout', io.StringIO()):
                with self.assertRaises(RuntimeError): full_workflow.main()
            report = json.loads(next((root / 'artifacts/diagnostics').glob('*/result.json')).read_text())
            self.assertEqual(len(calls), 2)
            self.assertTrue(report['output_state_verification_required'])
            self.assertFalse(report['automatic_retry_performed'])
            self.assertFalse(report['success'])
            self.assertIsNone(report['final_output'])


if __name__ == '__main__': unittest.main()
