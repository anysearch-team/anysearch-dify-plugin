"""检查真实开发包内容与工作树一致，不替代安装或 Marketplace 校验。"""
import hashlib
import argparse
import json
from pathlib import Path
import posixpath
import re
import subprocess
from urllib.parse import unquote, urlsplit
import zipfile

import yaml


def check_readme_links(archive: zipfile.ZipFile, names: set[str]) -> None:
    repo_prefix = 'https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/'
    for document in ('README.md', 'PRIVACY.md', 'readme/README_zh_Hans.md'):
        content = archive.read(document).decode('utf-8')
        for link in re.findall(r'\]\(([^)]+)\)', content):
            url = urlsplit(link)
            if url.scheme:
                if link.startswith(repo_prefix):
                    source = unquote(link[len(repo_prefix):].split('#', 1)[0])
                    assert Path(source).is_file(), f'Broken source link in {document}: {link}'
                continue
            destination = posixpath.normpath(posixpath.join(posixpath.dirname(document), unquote(url.path)))
            assert destination in names, f'Broken package link in {document}: {link}'


PACKAGE_FILES = {'.difyignore', 'manifest.yaml', 'main.py', 'requirements.txt', 'README.md', 'PRIVACY.md',
               'readme/README_zh_Hans.md', 'LICENSE', '_assets/icon.png',
               'provider/__init__.py', 'provider/anysearch.py', 'provider/anysearch.yaml',
               'tools/__init__.py', 'tools/search.py', 'tools/search.yaml', 'tools/discover.py', 'tools/discover.yaml',
               'tools/vertical_search.py', 'tools/vertical_search.yaml',
               'tools/extract.py', 'tools/extract.yaml',
               *('anysearch/' + name for name in ('__init__.py','catalog.py','client.py','errors.py','models.py','output.py','response.py','validation.py','semantic_rules.py'))}


def check(package: Path, target: Path) -> dict:
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert set(names) == PACKAGE_FILES and len(names) == len(PACKAGE_FILES), 'Unexpected or missing package files'
        content_hash = hashlib.sha256()
        source_commit_reproduces_package = True
        for name in names:
            assert archive.read(name) == Path(name).read_bytes(), 'Package differs from current source: ' + name
        for name in sorted(names):
            content = archive.read(name)
            content_hash.update(name.encode('utf-8') + b'\0' + len(content).to_bytes(8, 'big') + content)
            committed = subprocess.run(['git', 'show', f'HEAD:{name}'], capture_output=True, check=False)
            if committed.returncode != 0 or committed.stdout != content:
                source_commit_reproduces_package = False
        manifest = yaml.safe_load(archive.read('manifest.yaml'))
        provider = yaml.safe_load(archive.read('provider/anysearch.yaml'))
        assert manifest['plugins']['tools'] == ['provider/anysearch.yaml']
        assert provider['tools'] == ['tools/search.yaml', 'tools/discover.yaml', 'tools/vertical_search.yaml', 'tools/extract.yaml']
        assert provider['extra']['python']['source'] in names
        for tool in provider['tools']:
            metadata = yaml.safe_load(archive.read(tool))
            assert metadata['extra']['python']['source'] in names
        assert provider['credentials_for_provider']['api_key']['required'] is False
        check_readme_links(archive, set(names))
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            destination = (target/name).resolve()
            assert destination.is_relative_to(target)
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(archive.read(name))
    return {'sha256':hashlib.sha256(package.read_bytes()).hexdigest(), 'files':names,
            'registered_tools':['search', 'discover', 'vertical_search', 'extract'], 'package_matches_worktree':True,
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=all'])),
            'source_commit_reproduces_package':source_commit_reproduces_package,
            'package_content_sha256':content_hash.hexdigest(),
            'unpacked_to':str(target),'installation_verified':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('.codex/package-check'))
    args = parser.parse_args()
    report=check(args.directory/'anysearch-0.1.0.difypkg', args.directory/'unpacked')
    (args.directory/'package-check.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
