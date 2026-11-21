import json
import logging
import os
import platform
import sys
import threading
import traceback
import urllib2
import uuid
from urlparse import urlsplit

from ubuntutweak.common.consts import VERSION
from ubuntutweak.common.metadata import get_build_metadata

log = logging.getLogger('diagnostics.sentry')

_CLIENT = None
_OLD_EXCEPTHOOK = None
_LOCK = threading.Lock()


def _parse_dsn(dsn):
    # https://<key>@<host>/<project_id>
    if not dsn:
        return None
    try:
        parts = urlsplit(dsn)
        if parts.scheme not in ('http', 'https'):
            return None
        project_id = parts.path.strip('/').split('/')[-1]
        if not project_id or not parts.username:
            return None
        store_url = '%s://%s/api/%s/store/' % (parts.scheme, parts.netloc.split('@')[-1], project_id)
        return {
            'dsn': dsn,
            'key': parts.username,
            'secret': parts.password,
            'store_url': store_url,
            'project_id': project_id,
        }
    except Exception, e:
        log.debug('Invalid Sentry DSN: %s', e)
        return None


def _scrub_text(text):
    if not text:
        return text
    try:
        home = os.path.expanduser('~')
        if home and home in text:
            text = text.replace(home, '~')
    except Exception:
        pass
    return text


class SentryReporter(object):
    def __init__(self, dsn, channel='stable'):
        self.config = _parse_dsn(dsn)
        self.channel = channel
        self.metadata = get_build_metadata()
        self.enabled = bool(self.config)

    def _build_event(self, exc_info=None, message=None, extra=None):
        exc_type, exc_value, tb = exc_info or (None, None, None)
        event = {
            'event_id': uuid.uuid4().hex,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
            'platform': 'python',
            'level': 'error',
            'logger': 'ubuntutweak',
            'release': '%s' % self.metadata.get('version', VERSION),
            'environment': self.channel,
            'tags': {
                'channel': self.channel,
                'version': self.metadata.get('version', VERSION),
                'commit': self.metadata.get('commit', ''),
                'python': platform.python_version(),
                'platform': platform.platform(),
            },
            'extra': {
                'build': self.metadata,
            },
            'user': {},  # intentionally empty; opt-in diagnostics should avoid PII
        }
        if extra:
            safe_extra = {}
            for k, v in extra.items():
                safe_extra[k] = _scrub_text(str(v))
            event['extra'].update(safe_extra)

        if exc_type is not None:
            frames = []
            for filename, lineno, func, ctx in traceback.extract_tb(tb):
                frames.append({
                    'filename': _scrub_text(filename),
                    'abs_path': _scrub_text(filename),
                    'function': func,
                    'lineno': lineno,
                    'context_line': _scrub_text(ctx or ''),
                })
            event['exception'] = {
                'values': [{
                    'type': getattr(exc_type, '__name__', str(exc_type)),
                    'value': _scrub_text(str(exc_value)),
                    'stacktrace': {'frames': frames},
                }]
            }
            event['message'] = _scrub_text(str(exc_value))
        elif message:
            event['message'] = _scrub_text(str(message))
        return event

    def send(self, exc_info=None, message=None, extra=None):
        if not self.enabled:
            return False
        event = self._build_event(exc_info=exc_info, message=message, extra=extra)
        body = json.dumps(event)
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'ubuntu-tweak-sentry/0.1',
            'X-Sentry-Auth': 'Sentry sentry_version=7, sentry_client=ubuntu-tweak/%s, sentry_key=%s' % (VERSION, self.config['key']),
        }
        if self.config.get('secret'):
            headers['X-Sentry-Auth'] += ', sentry_secret=%s' % self.config['secret']

        def _post():
            try:
                req = urllib2.Request(self.config['store_url'], data=body, headers=headers)
                resp = urllib2.urlopen(req, timeout=5)
                try:
                    resp.read()
                finally:
                    resp.close()
                log.debug('Sentry event sent: %s', event.get('event_id'))
            except Exception, e:
                log.debug('Sentry send failed: %s', e)

        t = threading.Thread(target=_post)
        t.daemon = True
        t.start()
        return True


def init_error_reporting(enabled=None, dsn=None, channel='stable', force=False):
    """Initialize lightweight Sentry reporting.

    Enabled by default only when channel is nightly and a DSN is present, unless
    explicitly enabled. This avoids unexpected telemetry in stable installs.
    """
    global _CLIENT, _OLD_EXCEPTHOOK

    metadata = get_build_metadata()
    channel = channel or metadata.get('channel', 'stable')
    dsn = dsn or os.environ.get('UBUNTU_TWEAK_SENTRY_DSN') or metadata.get('sentry_dsn', '')

    if enabled is None:
        enabled = str(channel).lower() == 'nightly' and bool(dsn)
    if not dsn:
        enabled = False

    with _LOCK:
        if not enabled and not force:
            _CLIENT = None
            return None

        _CLIENT = SentryReporter(dsn, channel=channel)
        if not _CLIENT.enabled:
            _CLIENT = None
            return None

        if _OLD_EXCEPTHOOK is None:
            _OLD_EXCEPTHOOK = sys.excepthook

            def _hook(exc_type, exc_value, tb):
                try:
                    capture_exception((exc_type, exc_value, tb))
                except Exception:
                    pass
                try:
                    _OLD_EXCEPTHOOK(exc_type, exc_value, tb)
                except Exception:
                    pass

            sys.excepthook = _hook
            log.info('Sentry error reporting enabled for channel=%s', channel)
        return _CLIENT


def capture_exception(exc_info=None, extra=None):
    global _CLIENT
    client = _CLIENT
    if not client:
        return False
    try:
        return client.send(exc_info=exc_info or sys.exc_info(), extra=extra)
    except Exception:
        return False
