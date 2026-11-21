# -*- coding: utf-8 -*-
"""Seed an isolated --data-dir for the candidate EXE GUI acceptance.

Creates a sample directory source (registered, selected, state='new') with many
files so a directory build takes a few seconds (for minimize/restore-while-scan).
"""
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'search_app'))

from discovery.scheduler import DiscoveryController  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    _ROOT, '.tmp', 'discovery-integration', 'gui-data')
FILES = int(sys.argv[2]) if len(sys.argv) > 2 else 15000
SAMPLE = os.path.join(DATA, 'sample')
os.makedirs(SAMPLE, exist_ok=True)
for i in range(FILES):
    with open(os.path.join(SAMPLE, f'f{i:05d}.txt'), 'w', encoding='utf-8') as f:
        f.write('x')

controller = DiscoveryController(os.path.join(DATA, 'catalog.db'),
                                 os.path.join(DATA, 'discovery.json'))
sid = controller.add_directory(SAMPLE)
controller.select(sid, True)
controller._config['auto_scan'] = True
controller.save()
controller.shutdown()
print(json.dumps({'data_dir': DATA, 'sample': SAMPLE, 'source_id': sid,
                  'files': FILES}, ensure_ascii=False, indent=2))
