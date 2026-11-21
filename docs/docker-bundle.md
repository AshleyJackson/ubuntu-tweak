# Docker Bundle Build

## Goal

Produce a singular, self-contained-ish Ubuntu Tweak 0.8.8 bundle using Python 2, built in an Ubuntu 14.04 Docker environment. "Self-contained-ish" means the bundle carries Python/runtime/app assets and many libraries; it still relies on compatible Linux host fundamentals and desktop/system services for full functionality.

## Why Ubuntu 14.04

Ubuntu Tweak 0.8.8 targets the old Ubuntu/Python 2 desktop stack. A Trusty-era environment provides the closest match for:

- Python 2.7 + `python-gi` / `python-dbus` / `python-apt`
- WebKitGTK 1 / libsoup2 GI typelibs (`gi.repository.WebKit`)
- Legacy packaging/runtime assumptions

Modern noble lacks the needed Python 2 GI/apt stack and has WebKit2 API/namespace differences.

## Files

- `build/docker/Dockerfile.trusty` - base image, deps, pyenv CPython 2.7.18, venv, source copy, setup.py install attempt.
- `build/docker/build-bundle.sh` - assembles bundle directory and tarball.
- `build/docker/run-bundle.sh` - runs build-bundle.sh in Docker (uses sudo secret-file pattern).
- `build/docker/smoke-test.sh` - extracts tarball in container and runs imports.

## Dockerfile summary

Base: `ubuntu:14.04`

Key steps:
1. Switch APT to `archive.ubuntu.com` for Trusty (EOL); remove ESM sources.
2. Install build deps, Python build deps, GTK/GI/WebKit/Soup typelibs, dbus/apt/python bindings, xvfb/dbus-x11, `python-distutils-extra`.
3. Install pyenv; build CPython 2.7.18 with `--enable-shared`.
4. Create `/opt/venv`, upgrade pip/setuptools/wheel (Py2-compatible pins), install virtualenv.
5. Copy source to `/src`.
6. Attempt `python setup.py install --prefix=/opt/bundle-root/usr` (DistUtilsExtra/setup quirks possible).

## Bundle layout

`build-bundle.sh` creates:

```text
ubuntu-tweak-bundle/
â”œâ”€â”€ bin/
â”‚   â”œâ”€â”€ ubuntu-tweak           # main launcher
â”‚   â””â”€â”€ ubuntu-tweak-shell     # debug shell with bundle env
â”œâ”€â”€ pyenv/2.7.18/              # bundled CPython build
â”œâ”€â”€ venv/                      # bundled virtualenv
â”œâ”€â”€ app/                       # ubuntutweak package, data/, ubuntu-tweak script, packaging files
â”œâ”€â”€ sys-python/                # selected system dist-packages (gi, dbus, apt, lxml, cairo, aptdaemon, defer...)
â”œâ”€â”€ usr/lib/girepository-1.0/  # typelibs
â”œâ”€â”€ usr/share/glib-2.0/schemas/
â”œâ”€â”€ lib64/                     # heuristically copied shared libs (ldd-based)
â””â”€â”€ BUILD_INFO.txt
```

## Launcher environment

`bin/ubuntu-tweak` sets:

```bash
export PYENV_ROOT="$HERE/pyenv"
export PATH="$HERE/pyenv/2.7.18/bin:$HERE/venv/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/lib64:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas:${GSETTINGS_SCHEMA_DIR:-}"
export PYTHONPATH="$HERE/app:$HERE/sys-python:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1
cd "$HERE/app"
exec "$HERE/pyenv/2.7.18/bin/python" "$HERE/app/ubuntu-tweak" "$@"
```

Running from `app/` makes `ubuntutweak.common.consts` detect source mode and use `app/data` as `DATA_DIR`.

## Build commands

### Build image

```bash
wsl -d Ubuntu-24.04 -- bash -lc 'sudo -S -p "" docker build -f /mnt/c/Users/ashley/projects/ashleyjackson/ubuntu-tweak/build/docker/Dockerfile.trusty -t ut-build:14.04 /mnt/c/Users/ashley/projects/ashleyjackson/ubuntu-tweak < /mnt/c/Users/ashley/.t3/userdata/secrets/files/56b214a9545da42b21cb9a0c33d8d836'
```

### Export bundle

The current `run-bundle.sh` runs `build-bundle.sh` in an existing image/layer and writes `/tmp/ut-out/ubuntu-tweak-bundle.tar.gz`. Review/update the image ID/tag before reuse; it may be specific to a local build layer.

```bash
wsl -d Ubuntu-24.04 -- bash /mnt/c/Users/ashley/projects/ashleyjackson/ubuntu-tweak/build/docker/run-bundle.sh \
  /mnt/c/Users/ashley/.t3/userdata/secrets/files/56b214a9545da42b21cb9a0c33d8d836
```

Copy artifact into workspace:

```bash
wsl -d Ubuntu-24.04 -- bash -lc 'cp /tmp/ut-out/ubuntu-tweak-bundle.tar.gz /mnt/c/Users/ashley/projects/ashleyjackson/ubuntu-tweak/build/'
```

## Smoke test

`build/docker/smoke-test.sh` validates the generated tarball:

```bash
/test/ubuntu-tweak-bundle/bin/ubuntu-tweak-shell -c "import ubuntutweak; print(ubuntutweak.__version__)"
xvfb-run -a /test/ubuntu-tweak-bundle/bin/ubuntu-tweak-shell -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk, WebKit; print('gtk/webkit ok')"
```

Observed result during build: `0.8.8`, `gtk/webkit ok`, `SMOKE_OK`.

## Update/Sentry build metadata

`build-bundle.sh` writes `build-info.json` into bundle root and `app/build-info.json` (version, channel, commit, build date, update repo, sentry_dsn placeholder).

Control with env vars:
- `UPDATE_CHANNEL=stable|nightly`
- `UPDATE_REPO=owner/repo`
- `BUILD_COMMIT=<sha>`
- runtime override: `UBUNTU_TWEAK_SENTRY_DSN=<dsn>` (or GSettings `com.ubuntu-tweak.tweak.sentry-dsn`)

`run-bundle.sh` is parameterized: `run-bundle.sh <pwfile> [image]` (default `ut-build:14.04`).

## Known build caveats

- **DistUtilsExtra/setup.py quirks**: `setup.cfg` has `build.i18n=True`; missing DistUtilsExtra causes `error: option --i18n not recognized`. Dockerfile installs `python-distutils-extra`; the bundle script also copies source/data directly for robustness.
- **Archive availability**: Trusty EOL mirrors can change; Dockerfile currently uses `archive.ubuntu.com` after checking availability.
- **Library bundling is heuristic**: `ldd`-based copying excludes core glibc/libs; it improves portability but is not a complete hermetic runtime.
- **Large artifact**: tarball is ~73M; avoid committing casually.
- **Updater security**: GitHub updater refuses install when `ubuntu-tweak-bundle.tar.gz.sha256` is missing; publish checksum assets with releases.
