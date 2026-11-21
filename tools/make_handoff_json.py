# -*- coding: utf-8 -*-
"""Generate docs/discovery_integration/handoff.json with final hashes/sizes."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def file_entry(rel):
    path = ROOT / rel
    return {'path': rel, 'bytes': path.stat().st_size, 'sha256': sha256(path)}


deliverables = [
    'docs/discovery_integration/IMPLEMENTATION.md',
    'docs/discovery_integration/FUNCTION_MAP.md',
    'docs/discovery_integration/VALIDATION.md',
    'docs/discovery_integration/MULTIMODAL_NEXT.md',
    'docs/discovery_integration/HANDOFF.md',
]

modules = [
    'search_app/discovery/scheduler.py',
    'search_app/discovery/native_scan.py',
    'search_app/discovery/catalog.py',
    'search_app/qt_frontend/discovery_ui.py',
    'search_app/qt_frontend/window.py',
    'search_app/qt_frontend/minimal_ui.py',
    'search_app/qt_frontend/tools.py',
]

tests = [
    'search_app/tests/test_discovery_scheduler.py',
    'search_app/tests/test_discovery_native.py',
    'search_app/tests/test_discovery_ui.py',
    'search_app/tests/test_qt_frontend.py',
]

candidate_manifest = json.loads((ROOT / 'dist/discovery-candidate/镜仔搜索新版/build-manifest.json').read_text(encoding='utf-8'))
protected = json.loads((ROOT / '.tmp/discovery-integration/protected-end.json').read_text(encoding='utf-8-sig'))

handoff = {
    'round': 'discovery-integration-0.1',
    'date': '2026-10-28',
    'status': 'runnable-candidate-pending-codex-review',
    'module_version': '0.2.0',
    'schema_version': 2,
    'api_note': 'DiscoveryService API unchanged; Catalog.search added dedupe_paths kwarg (default False); Catalog.remove_source added.',
    'tests': {
        'baseline_total': 289,
        'baseline_discovery': 74,
        'current_total': 305,
        'new_tests': 16,
        'failures': 0,
        'errors': 0,
        'skipped': 0,
        'log': '.tmp/discovery-integration/full-tests-final.log',
    },
    'candidate': {
        'exe': candidate_manifest['candidate_exe'],
        'exe_sha256': candidate_manifest['candidate_exe_sha256'],
        'exe_bytes': candidate_manifest['candidate_exe_bytes'],
        'built_at': candidate_manifest['built_at'],
        'build_manifest': 'dist/discovery-candidate/镜仔搜索新版/build-manifest.json',
        'build_command': '.venv-qt\\Scripts\\python.exe tools\\build_candidate.py',
        'launch': 'dist\\discovery-candidate\\镜仔搜索新版\\镜仔搜索新版.exe --data-dir ".\\.tmp\\discovery-integration\\gui-data"',
    },
    'deliverables': {rel: file_entry(rel) for rel in deliverables},
    'modules': {rel: file_entry(rel) for rel in modules},
    'tests_files': {rel: file_entry(rel) for rel in tests},
    'protected_unchanged': protected['files'],
    'library_unchanged': protected.get('library'),
    'real_verified': [
        '305 automatic tests (289 baseline + 16 new)',
        'real sample end-to-end increment (rename/delete/move-in, boundary)',
        'F: real bounded USN read 1000 records',
        '100k synthetic catalog re-test',
        'candidate EXE isolated launch, range panel, auto-scan 60000 files',
        'candidate EXE 20x minimize/restore (Ghost ShowWindow)',
        'candidate EXE single-instance forward + explore search/result/preview',
    ],
    'simulated_only': [
        'native full enumeration via synthetic MFT records (test_discovery_native)',
        'USN malformed/unknown records, cross-chunk, journal gaps (inherited from prework; unchanged)',
    ],
    'not_done': [
        'full real F: volume native build (only bounded 1000-record capability)',
        'real Ollama model Q&A, web search, mobile pairing (external service)',
        'multimodal image/OCR/video/audio retrieval (design only in MULTIMODAL_NEXT.md)',
    ],
}

out = ROOT / 'docs/discovery_integration/handoff.json'
out.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'written': str(out), 'deliverable_sha256': sha256(out), 'bytes': out.stat().st_size}, ensure_ascii=False, indent=2))
