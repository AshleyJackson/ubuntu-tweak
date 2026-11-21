import json
import os

from ubuntutweak.common.consts import DATA_DIR, IS_INSTALLED, VERSION


DEFAULT_METADATA = {
    'version': VERSION,
    'channel': 'stable',
    'commit': '',
    'build_date': '',
    'update_repo': 'AshleyJackson/ubuntu-tweak',
    'sentry_dsn': '',
}


def _candidate_paths():
    paths = []
    # Installed/bundle layout: <prefix>/share/ubuntu-tweak/build-info.json
    paths.append(os.path.join(os.path.dirname(DATA_DIR.rstrip('/\\')), 'build-info.json'))
    paths.append(os.path.join(DATA_DIR, 'build-info.json'))
    # Source/dev layout: repo root or build/
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    paths.append(os.path.join(root, 'build-info.json'))
    paths.append(os.path.join(root, 'build', 'build-info.json'))
    # Bundle env
    env = os.environ.get('UT_BUILD_INFO')
    if env:
        paths.insert(0, env)
    return paths


def get_build_metadata():
    metadata = dict(DEFAULT_METADATA)
    for path in _candidate_paths():
        try:
            if path and os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    metadata.update(data)
                break
        except Exception:
            continue
    metadata['is_installed'] = IS_INSTALLED
    return metadata


def get_channel():
    return get_build_metadata().get('channel', 'stable') or 'stable'


def is_nightly():
    return str(get_channel()).lower() == 'nightly'
