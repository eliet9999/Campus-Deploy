import io
import json
import stat
import tarfile
import zipfile
import pytest

from campus.config import Settings
from campus.safety import Rejected, github_url, safe_name, extract_zip, extract_artifact, detect, collect_static


@pytest.mark.parametrize('url', [
    'http://github.com/a/b', 'https://user:pass@github.com/a/b', 'https://github.com:443/a/b',
    'file:///etc/passwd', 'ssh://github.com/a/b', 'https://127.0.0.1/a/b',
    'https://github.com.evil.test/a/b', 'https://github.com/a/b?x=1',
    'https://github.com/a/b/tree/main', 'https://github.com/-x/repo', 'https://github.com/a/%2e%2e',
])
def test_reject_url(url):
    with pytest.raises(Rejected):
        github_url(url)


def test_normalize_git():
    assert github_url('https://github.com/octocat/Spoon-Knife.git/') == 'https://github.com/octocat/Spoon-Knife'


@pytest.mark.parametrize('path', ['/etc/passwd', '../a', 'a/../../x', 'C:/Windows/a', 'a\\b', 'x:ads', 'a//b', './x', 'NUL.txt', 'x/CON', 'a./b', 'a /b', 'x\x00y'])
def test_bad_paths(path):
    with pytest.raises(Rejected):
        safe_name(path)


def zip_at(tmp, entries):
    path = tmp / 'input.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def test_zip_symlink(tmp_path):
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    path = zip_at(tmp_path, [(link, '/etc/passwd')])
    with pytest.raises(Rejected, match='링크'):
        extract_zip(path, tmp_path / 'out', Settings())


def test_zip_duplicate(tmp_path):
    path = zip_at(tmp_path, [('a.txt', 'one'), ('A.txt', 'two')])
    with pytest.raises(Rejected, match='중복'):
        extract_zip(path, tmp_path / 'out', Settings())


def test_zip_bomb_limit(tmp_path):
    path = zip_at(tmp_path, [('big.txt', '0' * 200)])
    with pytest.raises(Rejected, match='크기'):
        extract_zip(path, tmp_path / 'out', Settings(extract_limit=100))


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'traversal', 'absolute', 'duplicate'])
def test_tar_hostile(tmp_path, kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        item = tarfile.TarInfo('dist/index.html')
        if kind in ('symlink', 'hardlink'):
            item.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
            item.linkname = '/etc/passwd'
        if kind == 'traversal':
            item.name = 'dist/../secret'
        if kind == 'absolute':
            item.name = '/etc/secret'
        tar.addfile(item)
        if kind == 'duplicate':
            tar.addfile(item)
    data.seek(0)
    with pytest.raises(Rejected):
        extract_artifact(data, tmp_path / 'out', Settings())


def test_static_secrets_and_size(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    (source / 'index.html').write_text('ok')
    for name in ('.env', '.env.production', 'private.key', 'id_rsa', 'campus.sqlite3'):
        (source / name).write_text('SECRET')
    collect_static(source, tmp_path / 'out', Settings())
    assert [p.name for p in (tmp_path / 'out').iterdir()] == ['index.html']
    with pytest.raises(Rejected):
        collect_static(source, tmp_path / 'tiny', Settings(artifact_limit=1))


@pytest.mark.parametrize('package', [
    {'dependencies': {'next': '1'}}, {'dependencies': {'express': '1'}},
    {'workspaces': ['apps/*']}, {'scripts': {'build': 'vite build --ssr'}, 'devDependencies': {'vite': '1'}},
])
def test_unsupported_presets(tmp_path, package):
    (tmp_path / 'package.json').write_text(json.dumps(package))
    (tmp_path / 'package-lock.json').write_text('{"lockfileVersion":3}')
    (tmp_path / 'index.html').write_text('must not be treated as static')
    with pytest.raises(Rejected):
        detect(tmp_path)
