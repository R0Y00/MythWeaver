import json

import pytest

from mythweaver import package_sources
from mythweaver.definitions import DomainError


def test_project_package_source_supports_powershell_utf8_bom(tmp_path,monkeypatch):
    monkeypatch.setattr(package_sources,'ROOT',tmp_path)
    assert package_sources.pip_arguments()==[]
    folder=tmp_path/'.runtime';folder.mkdir()
    (folder/'package-sources.json').write_text(json.dumps({'pip_index_url':'https://mirrors.aliyun.com/pypi/simple/'}),encoding='utf-8-sig')
    assert package_sources.pip_arguments()==['--index-url','https://mirrors.aliyun.com/pypi/simple/']


@pytest.mark.parametrize('url',['http://example.org/simple','https://user:secret@example.org/simple','https://example.org/simple?token=secret','--extra-index-url'])
def test_package_source_rejects_credentials_or_non_https(tmp_path,monkeypatch,url):
    monkeypatch.setattr(package_sources,'ROOT',tmp_path)
    folder=tmp_path/'.runtime';folder.mkdir()
    (folder/'package-sources.json').write_text(json.dumps({'pip_index_url':url}))
    with pytest.raises(DomainError) as exc:package_sources.pip_arguments()
    assert 'secret' not in str(exc.value)
