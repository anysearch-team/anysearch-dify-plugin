import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from scripts.check_package import PACKAGE_FILES, check


@pytest.mark.parametrize('junction', [False, pytest.param(True, marks=pytest.mark.skipif(
    os.name != 'nt', reason='Windows Junction regression'))])
def test_cli_excludes_development_state(tmp_path, junction):
    root = Path.cwd()
    cli = os.environ.get('DIFY_CLI') or shutil.which('dify')
    if not cli:
        pytest.skip('Set DIFY_CLI to the official Dify CLI executable')
    stage = tmp_path / 'plugin'
    stage.mkdir()
    for name in PACKAGE_FILES:
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
    graph = stage / '.codegraph'
    if junction:
        target = tmp_path / 'graph-target'
        target.mkdir()
        process = subprocess.run(
            ['powershell.exe', '-NoProfile', '-Command',
             'New-Item -ItemType Junction -Path $env:PACKAGE_GRAPH_LINK '
             '-Target $env:PACKAGE_GRAPH_TARGET | Out-Null'],
            env={**os.environ, 'PACKAGE_GRAPH_LINK': str(graph),
                 'PACKAGE_GRAPH_TARGET': str(target)},
            capture_output=True, text=True, timeout=30,
        )
        assert process.returncode == 0, process.stderr
    else:
        graph.mkdir()
    (graph / 'sentinel.json').write_text('{"development": true}', encoding='utf-8')
    state = stage / '.omo/run-continuation'
    state.mkdir(parents=True)
    (state / 'sentinel.json').write_text('{"development": true}', encoding='utf-8')
    package = tmp_path / 'regression.difypkg'
    process = subprocess.run(
        [str(Path(cli).resolve()), 'plugin', 'package', '.', '-o', str(package)],
        cwd=stage, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == PACKAGE_FILES
        assert len(archive.namelist()) == len(PACKAGE_FILES)
        for name in PACKAGE_FILES:
            assert archive.read(name) == (root / name).read_bytes()


def test_package_provenance_marks_uncommitted_contents(tmp_path, monkeypatch):
    root = Path.cwd()
    stage = tmp_path / 'source'
    stage.mkdir()
    for name in PACKAGE_FILES:
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
    subprocess.run(['git', 'init', '-q'], cwd=stage, check=True)
    subprocess.run(['git', 'add', '.'], cwd=stage, check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'baseline'], cwd=stage, check=True)
    monkeypatch.chdir(stage)
    monkeypatch.setattr('scripts.check_package.check_readme_links', lambda archive, names: None)

    def package_at(path):
        with zipfile.ZipFile(path, 'w') as archive:
            for name in sorted(PACKAGE_FILES):
                archive.write(name)

    package = tmp_path / 'source.difypkg'
    package_at(package)
    clean = check(package, tmp_path / 'clean-unpacked')
    assert not clean['worktree_dirty']
    assert clean['source_commit_reproduces_package']
    assert len(clean['package_content_sha256']) == 64

    (stage / 'main.py').write_bytes((stage / 'main.py').read_bytes() + b'\n')
    package_at(package)
    dirty = check(package, tmp_path / 'dirty-unpacked')
    assert dirty['worktree_dirty']
    assert not dirty['source_commit_reproduces_package']
    assert dirty['source_commit'] == clean['source_commit']
    assert dirty['package_content_sha256'] != clean['package_content_sha256']
