# Source from league-first-blood/: source scripts/env.sh
# Keep runtime/configuration writes out of read-only home directories.
export XDG_CACHE_HOME="$PWD/.local/cache"
export JUPYTER_CONFIG_DIR="$PWD/.local/jupyter-config"
export JUPYTER_DATA_DIR="$PWD/.local/jupyter-data"
export JUPYTER_RUNTIME_DIR="$PWD/.local/jupyter-runtime"
export IPYTHONDIR="$PWD/.local/ipython"
export MPLCONFIGDIR="$PWD/.local/matplotlib"
mkdir -p "$XDG_CACHE_HOME" "$JUPYTER_CONFIG_DIR" "$JUPYTER_DATA_DIR" \
  "$JUPYTER_RUNTIME_DIR" "$IPYTHONDIR" "$MPLCONFIGDIR"
chmod 700 "$JUPYTER_RUNTIME_DIR"
