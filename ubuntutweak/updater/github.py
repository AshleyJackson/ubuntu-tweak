import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import urllib2

from ubuntutweak.common.consts import VERSION
from ubuntutweak.common.metadata import get_build_metadata

log = logging.getLogger('updater.github')

API_BASE = 'https://api.github.com'
DEFAULT_REPO = 'AshleyJackson/ubuntu-tweak'
ASSET_NAME = 'ubuntu-tweak-bundle.tar.gz'
SHA256_NAME = 'ubuntu-tweak-bundle.tar.gz.sha256'


class UpdateError(Exception):
    pass


def _parse_version(value):
    m = re.findall(r'(\d+)', str(value))
    if not m:
        return (0,)
    return tuple(int(x) for x in m)


def _http_get(url, headers=None, timeout=15):
    req_headers = {
        'User-Agent': 'ubuntu-tweak-updater/%s' % VERSION,
        'Accept': 'application/vnd.github+json',
    }
    if headers:
        req_headers.update(headers)
    req = urllib2.Request(url, headers=req_headers)
    resp = urllib2.urlopen(req, timeout=timeout)
    try:
        return resp.read(), resp.info()
    finally:
        resp.close()


def _download(url, dest, timeout=60):
    data, _info = _http_get(url, timeout=timeout)
    with open(dest, 'wb') as f:
        f.write(data)
    return dest


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_sha256(asset_path, sha_path):
    if not sha_path or not os.path.exists(sha_path):
        return False, 'missing checksum'
    with open(sha_path, 'r') as f:
        content = f.read().strip()
    expected = content.split()[0].lower()
    actual = _sha256(asset_path).lower()
    if expected != actual:
        return False, 'checksum mismatch: expected %s got %s' % (expected, actual)
    return True, actual


class GitHubUpdater(object):
    def __init__(self, repo=None, channel='stable', current_version=None):
        meta = get_build_metadata()
        self.repo = repo or meta.get('update_repo') or DEFAULT_REPO
        self.channel = (channel or meta.get('channel') or 'stable').lower()
        self.current_version = current_version or meta.get('version') or VERSION

    def api_url(self, path):
        return '%s/repos/%s%s' % (API_BASE, self.repo, path)

    def get_release(self):
        if self.channel == 'nightly':
            data, _ = _http_get(self.api_url('/releases?per_page=20'))
            releases = json.loads(data)
            for rel in releases:
                if rel.get('draft'):
                    continue
                if rel.get('prerelease'):
                    return rel
            raise UpdateError('No nightly prerelease found')
        else:
            data, _ = _http_get(self.api_url('/releases/latest'))
            rel = json.loads(data)
            if rel.get('draft'):
                raise UpdateError('Latest release is draft')
            return rel

    def _pick_asset(self, release, name):
        for asset in release.get('assets', []):
            if asset.get('name') == name:
                return asset
        return None

    def check(self):
        release = self.get_release()
        tag = release.get('tag_name') or release.get('name') or ''
        remote_version = tag.lstrip('v')
        current = _parse_version(self.current_version)
        remote = _parse_version(remote_version)
        asset = self._pick_asset(release, ASSET_NAME)
        sha_asset = self._pick_asset(release, SHA256_NAME)
        return {
            'update_available': remote > current,
            'channel': self.channel,
            'current_version': self.current_version,
            'remote_version': remote_version,
            'tag': tag,
            'name': release.get('name') or tag,
            'body': release.get('body') or '',
            'html_url': release.get('html_url') or '',
            'asset': asset,
            'sha256_asset': sha_asset,
            'release': release,
        }

    def download(self, info, dest_dir=None):
        asset = info.get('asset')
        if not asset:
            raise UpdateError('Release has no %s asset' % ASSET_NAME)
        dest_dir = dest_dir or tempfile.mkdtemp(prefix='ut-update-')
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        asset_path = os.path.join(dest_dir, ASSET_NAME)
        sha_path = None
        _download(asset['browser_download_url'], asset_path)
        sha_asset = info.get('sha256_asset')
        if sha_asset:
            sha_path = os.path.join(dest_dir, SHA256_NAME)
            _download(sha_asset['browser_download_url'], sha_path)
            ok, detail = _verify_sha256(asset_path, sha_path)
            if not ok:
                raise UpdateError(detail)
        else:
            log.warning('No SHA256 asset found for release; refusing unverified install')
            raise UpdateError('Release missing %s checksum asset' % SHA256_NAME)
        return {'asset_path': asset_path, 'sha256_path': sha_path, 'dest_dir': dest_dir}

    def install_bundle(self, asset_path, target_dir):
        """Install tarball bundle into a versioned release dir.

        This is a conservative installer: it extracts to target_dir.new and then
        swaps into place. It does not handle apt-managed installs.
        """
        if not os.path.exists(asset_path):
            raise UpdateError('Asset not found: %s' % asset_path)
        parent = os.path.dirname(target_dir.rstrip('/\\'))
        if not os.path.exists(parent):
            os.makedirs(parent)

        staging = target_dir + '.new'
        backup = target_dir + '.bak'
        if os.path.exists(staging):
            shutil.rmtree(staging)
        os.makedirs(staging)

        # Safe tar extraction for Python 2.7
        import tarfile
        tar = tarfile.open(asset_path, 'r:gz')
        try:
            members = tar.getmembers()
            for m in members:
                name = m.name
                if name.startswith('/') or '..' in name.replace('\\', '/').split('/'):
                    raise UpdateError('Unsafe path in tarball: %s' % name)
            tar.extractall(staging)
        finally:
            tar.close()

        # Expect a single top-level ubuntu-tweak-bundle dir; if present, move contents up
        entries = os.listdir(staging)
        if len(entries) == 1:
            inner = os.path.join(staging, entries[0])
            if os.path.isdir(inner):
                for item in os.listdir(inner):
                    shutil.move(os.path.join(inner, item), os.path.join(staging, item))
                os.rmdir(inner)

        if os.path.exists(backup):
            shutil.rmtree(backup)
        if os.path.exists(target_dir):
            shutil.move(target_dir, backup)
        shutil.move(staging, target_dir)
        return target_dir
