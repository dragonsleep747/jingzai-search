# -*- coding: utf-8 -*-
"""Run the full test suite and write UTF-8 log; avoid console encoding issues."""
import os
import platform
import runpy
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else '.tmp/discovery-integration/full-tests.log'
TEST_ARGS = sys.argv[2:]

if hasattr(platform, '_wmi_query'):
    def _skip_wmi(*args, **kwargs):
        raise OSError('WMI disabled for test runner')
    platform._wmi_query = _skip_wmi

os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
stream = open(OUT, 'w', encoding='utf-8')
old_stdout, old_stderr = sys.stdout, sys.stderr
sys.stdout = sys.stderr = stream
exit_code = 0
try:
    sys.argv = ['search_app/tests/run_tests.py', *TEST_ARGS]
    runpy.run_path('search_app/tests/run_tests.py', run_name='__main__')
except SystemExit as exc:
    exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
finally:
    sys.stdout, sys.stderr = old_stdout, old_stderr
    stream.close()
sys.exit(exit_code)
