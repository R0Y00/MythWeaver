"""Project-local package source; never inherit host credentials into plugin installs."""
import json
from urllib.parse import urlsplit

from .config import ROOT
from .definitions import DomainError


def pip_arguments():
    path=ROOT/'.runtime/package-sources.json'
    if not path.exists():return []
    try:
        config=json.loads(path.read_text(encoding='utf-8-sig'))
        value=config['pip_index_url']
        if not isinstance(value,str) or len(value)>1000:raise ValueError()
        url=urlsplit(value)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise ValueError()
    except (ValueError,TypeError,KeyError,OSError):
        raise DomainError('INVALID_PACKAGE_SOURCE','项目Python下载源配置无效，请检查.runtime/package-sources.json') from None
    return ['--index-url',value]
