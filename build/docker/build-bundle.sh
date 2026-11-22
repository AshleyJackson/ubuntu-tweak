#!/usr/bin/env bash
set -euo pipefail

# Run inside ubuntu:14.04 container. Builds a self-contained-ish bundle tarball.
# "Self-contained" here means: pyenv python2.7 + venv + app code + data + python deps + launchers
# bundled together; system GI/GTK/dbus libs are also copied in for portability on linux x86_64.

OUT=${1:-/out}
BUNDLE_NAME=${BUNDLE_NAME:-ubuntu-tweak-bundle}
BUNDLE="$OUT/$BUNDLE_NAME"
ROOT=/opt/bundle-root
VENV=/opt/venv
PYENV_ROOT=/opt/pyenv
PYVER=2.7.18

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE"/{bin,lib,libexec,share,python,env}

echo ">>> copying pyenv python"
mkdir -p "$BUNDLE/pyenv"
cp -a "$PYENV_ROOT/versions/$PYVER" "$BUNDLE/pyenv/$PYVER"

echo ">>> copying venv"
cp -a "$VENV" "$BUNDLE/venv"

echo ">>> copying app install prefix"
# setup.py installed into /opt/bundle-root/usr
if [ -d "$ROOT/usr" ]; then
  cp -a "$ROOT/usr/." "$BUNDLE/usr/" 2>/dev/null || true
fi
mkdir -p "$BUNDLE/usr/share" "$BUNDLE/usr/bin"

# Also copy source package + data directly for robustness (setup.py on 0.8.x is quirky)
mkdir -p "$BUNDLE/app"
cp -a /src/ubuntutweak "$BUNDLE/app/"
cp -a /src/data "$BUNDLE/app/"
cp -a /src/ubuntu-tweak "$BUNDLE/app/ubuntu-tweak"
cp -a /src/setup.py /src/setup.cfg /src/README /src/COPYING "$BUNDLE/app/" 2>/dev/null || true

echo ">>> copying system python deps not in venv"
# Copy gi, dbus, apt, xdg, lxml from system python2.7 dist-packages
SYS_DIST=/usr/lib/python2.7/dist-packages
mkdir -p "$BUNDLE/sys-python"
for p in gi dbus apt apt_pkg.so aptsources xdg lxml cairo _cairo.so; do
  if [ -e "$SYS_DIST/$p" ]; then cp -a "$SYS_DIST/$p" "$BUNDLE/sys-python/"; fi
done
# also gi from /usr/lib/python2.7/dist-packages/gi
# include aptdaemon bits if present
for p in aptdaemon defer; do
  if [ -e "$SYS_DIST/$p" ]; then cp -a "$SYS_DIST/$p" "$BUNDLE/sys-python/"; fi
done

echo ">>> copying typelibs and gsettings schemas"
mkdir -p "$BUNDLE/usr/lib/girepository-1.0" "$BUNDLE/usr/share/glib-2.0/schemas"
cp -a /usr/lib/girepository-1.0/*.typelib "$BUNDLE/usr/lib/girepository-1.0/" 2>/dev/null || true
cp -a /usr/lib/x86_64-linux-gnu/girepository-1.0/*.typelib "$BUNDLE/usr/lib/girepository-1.0/" 2>/dev/null || true
cp -a /usr/share/glib-2.0/schemas/* "$BUNDLE/usr/share/glib-2.0/schemas/" 2>/dev/null || true
if command -v glib-compile-schemas >/dev/null; then
  glib-compile-schemas "$BUNDLE/usr/share/glib-2.0/schemas" || true
fi

echo ">>> copying shared libs (best-effort, broad)"
# For a "fully self-contained" bundle we copy required .so files resolved by ldd
# from python, gi modules, and key libs. This is heuristic but practical.
mkdir -p "$BUNDLE/lib64"
copy_libs() {
  local target="$1"; shift
  for f in "$@"; do
    [ -e "$f" ] || continue
    while IFS= read -r line; do
      lib=$(echo "$line" | awk '/=> \//{print $3} /^\//{print $1}')
      [ -n "$lib" ] && [ -f "$lib" ] || continue
      base=$(basename "$lib")
      case "$base" in
        linux-vdso.so*|ld-linux*.so*|libc.so.*|libpthread.so.*|libm.so.*|libdl.so.*|librt.so.*|libresolv.so.*) ;;
        *) cp -n "$lib" "$BUNDLE/lib64/" 2>/dev/null || true ;;
      esac
    done < <(ldd "$f" 2>/dev/null || true)
  done
}

# Seed with key binaries/libs
copy_libs "$BUNDLE/lib64" "$BUNDLE/pyenv/$PYVER/bin/python" "$BUNDLE/pyenv/$PYVER/lib/libpython2.7.so"* || true
copy_libs "$BUNDLE/lib64" "$SYS_DIST"/apt_pkg.so "$SYS_DIST"/_cairo.so 2>/dev/null || true
copy_libs "$BUNDLE/lib64" /usr/lib/python2.7/dist-packages/gi/_gi.so 2>/dev/null || true
# GTK/GI stack
for lib in /usr/lib/x86_64-linux-gnu/libgtk-3.so* /usr/lib/x86_64-linux-gnu/libgdk-3.so* \
           /usr/lib/x86_64-linux-gnu/libwebkitgtk-3.0.so* /usr/lib/x86_64-linux-gnu/libjavascriptcoregtk-3.0.so* \
           /usr/lib/x86_64-linux-gnu/libsoup-2.4.so* /usr/lib/x86_64-linux-gnu/libnotify.so* \
           /usr/lib/x86_64-linux-gnu/libgirepository-1.0.so* /usr/lib/x86_64-linux-gnu/libgio-2.0.so* \
           /usr/lib/x86_64-linux-gnu/libglib-2.0.so* /usr/lib/x86_64-linux-gnu/libgobject-2.0.so* \
           /usr/lib/x86_64-linux-gnu/libdbus-1.so* /usr/lib/x86_64-linux-gnu/libdbus-glib-1.so* \
           /usr/lib/x86_64-linux-gnu/libapt-pkg.so*; do
  [ -e "$lib" ] && copy_libs "$BUNDLE/lib64" "$lib"
done

echo ">>> writing launcher"
cat > "$BUNDLE/bin/ubuntu-tweak" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYENV_ROOT="$HERE/pyenv"
export PATH="$HERE/pyenv/2.7.18/bin:$HERE/venv/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/lib64:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas:${GSETTINGS_SCHEMA_DIR:-}"
export PYTHONPATH="$HERE/app:$HERE/sys-python:${PYTHONPATH:-}"
# Avoid host user-site pollution
export PYTHONNOUSERSITE=1
cd "$HERE/app"
exec "$HERE/pyenv/2.7.18/bin/python" "$HERE/app/ubuntu-tweak" "$@"
LAUNCH
chmod +x "$BUNDLE/bin/ubuntu-tweak"

cat > "$BUNDLE/bin/ubuntu-tweak-shell" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYENV_ROOT="$HERE/pyenv"
export PATH="$HERE/pyenv/2.7.18/bin:$HERE/venv/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/lib64:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas:${GSETTINGS_SCHEMA_DIR:-}"
export PYTHONPATH="$HERE/app:$HERE/sys-python:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1
exec "$HERE/pyenv/2.7.18/bin/python" "$@"
LAUNCH
chmod +x "$BUNDLE/bin/ubuntu-tweak-shell"

echo ">>> metadata"
cat > "$BUNDLE/BUILD_INFO.txt" <<EOF
name=$BUNDLE_NAME
built_on=$(date -u +%Y-%m-%dT%H:%M:%SZ)
builder_image=ubuntu:14.04
python=$($BUNDLE/pyenv/$PYVER/bin/python --version 2>&1)
EOF

UPDATE_CHANNEL=${UPDATE_CHANNEL:-stable}
UPDATE_REPO=${UPDATE_REPO:-AshleyJackson/ubuntu-tweak}
BUILD_COMMIT=${BUILD_COMMIT:-}
cat > "$BUNDLE/build-info.json" <<EOF
{
  "version": "0.8.8",
  "channel": "$UPDATE_CHANNEL",
  "commit": "$BUILD_COMMIT",
  "build_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "update_repo": "$UPDATE_REPO",
  "sentry_dsn": ""
}
EOF
cp "$BUNDLE/build-info.json" "$BUNDLE/app/build-info.json"

echo ">>> creating tarball"
tar -C "$OUT" -czf "$OUT/${BUNDLE_NAME}.tar.gz" "$BUNDLE_NAME"
echo "BUNDLE_READY=$OUT/${BUNDLE_NAME}.tar.gz"
