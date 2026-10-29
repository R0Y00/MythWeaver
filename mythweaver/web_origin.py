"""Accept same-origin browser requests through whichever address serves the UI."""
from urllib.parse import urlsplit


def normalized(value):
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in ('','/') or parsed.query or parsed.fragment:return None
        return parsed.scheme,parsed.hostname.lower(),parsed.port or (443 if parsed.scheme=='https' else 80)
    except (ValueError,TypeError):return None


def automatic(configured):
    value=(configured or '').strip()
    if value.lower() in ('','auto','*'):return True
    origin=normalized(value)
    # Older instructions used a bind address as the public origin; treat it as auto.
    return bool(origin and origin[1] in ('0.0.0.0','::'))


def allowed(request,configured):
    origin=request.headers.get('origin')
    if origin is None:return True
    expected=str(request.url.replace(path='',query='')) if automatic(configured) else configured
    actual=normalized(origin)
    return actual is not None and actual==normalized(expected)


def secure_cookie(request,configured):
    return request.url.scheme=='https' if automatic(configured) else configured.lower().startswith('https:')
