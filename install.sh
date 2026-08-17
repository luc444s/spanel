#!/bin/bash
# installer systutor: kernel (systutor-core) + shell (systutor-shell)
# portable: Linux x86, ARM, Termux. Sin GNU-ismos. Sin dependencias fuera de git/pip/npm/python3.
set -euo pipefail

CORE_URL="${SYSTUTOR_CORE_URL:-https://github.com/luc444s/systutor-core.git}"
SHELL_URL="${SYSTUTOR_SHELL_URL:-https://github.com/luc444s/systutor-shell.git}"
CORE_PATH="vendor/systutor-core"
SHELL_PATH="vendor/systutor-shell"
VENV_DIR=".venv"

DO_CORE=1
DO_SHELL=1
DRY_RUN=0
CHECK_ONLY=0
WEB_DIR="apps/web"

usage() {
  cat <<'EOF'
Uso: install.sh [OPCIONES] [RUTA-PROYECTO]

Monta systutor-core (kernel Python) y systutor-shell (frontend TS) en un proyecto.

Opciones:
  --core          solo kernel (submodule + pip install -e)
  --shell         solo shell (submodule + alias vite/tsconfig + deps npm)
  --web-dir DIR   carpeta del frontend (default: apps/web)
  --check-arch    solo diagnostico del entorno (no toca nada)
  --dry-run       imprime acciones sin ejecutar
  -h, --help      esta ayuda
EOF
}

log()  { echo "[systutor-installer] $*"; }
warn() { echo "[systutor-installer] ADVERTENCIA: $*" >&2; }
step() { if [ "$DRY_RUN" = 1 ]; then log "DRY-RUN: $*"; else log "$*"; fi; }

is_termux() {
  [ -n "${PREFIX:-}" ] && case "$PREFIX" in *termux*) return 0;; esac
  return 1
}

have() { command -v "$1" >/dev/null 2>&1; }

venv_python() {
  if [ -x "$VENV_DIR/bin/python3" ]; then
    printf '%s\n' "$VENV_DIR/bin/python3"
  else
    printf '%s\n' "python3"
  fi
}

ensure_venv() {
  if ! have python3; then
    echo "error: python3 no encontrado" >&2
    exit 1
  fi
  if [ ! -x "$VENV_DIR/bin/python3" ]; then
    step "python3 -m venv $VENV_DIR"
    [ "$DRY_RUN" = 1 ] || python3 -m venv "$VENV_DIR"
  fi
  step "$VENV_DIR/bin/python3 -m pip install --upgrade pip"
  [ "$DRY_RUN" = 1 ] || "$VENV_DIR/bin/python3" -m pip install --upgrade pip
}

check_arch() {
  local arch
  arch="$(uname -m)"
  log "arquitectura: $arch"
  if is_termux; then
    log "entorno: Termux (libc bionic, python $(python3 -V 2>/dev/null | cut -d' ' -f2 || echo '?'))"
  else
    log "entorno: linux estandar ($(python3 -V 2>/dev/null || echo 'sin python3'))"
  fi
  have git && log "git: $(git --version)" || warn "git no encontrado"
  have python3 || warn "python3 no encontrado — kernel no se instalara"
  have pip3 || have pip || warn "pip no encontrado — kernel no se instalara"
  have node || warn "node no encontrado — shell no se instalara"
  have npm || warn "npm no encontrado — shell no se instalara"
  if is_termux; then
    # Termux usa bionic: asyncpg (extension C) debe compilar de fuente.
    # NO se instala glibc en Termux; se compila contra bionic.
    have clang || warn "clang no encontrado en Termux — asyncpg no compilara. Corre: pkg install clang"
    have pg_config || warn "pg_config no encontrado en Termux — asyncpg no compilara. Corre: pkg install postgresql"
  fi
}

add_submodule() {
  local url="$1" path="$2"
  if [ -f .gitmodules ] && grep -q "path = $path" .gitmodules; then
    step "submodule $path ya configurado, omito"
    return 0
  fi
  step "git submodule add $url $path"
  [ "$DRY_RUN" = 1 ] || git submodule add "$url" "$path"
}

python_patch() {
  # python_patch <modo> <archivo> <payload> — edicion portatil sin sed GNU
  local mode="$1" file="$2" payload="$3"
  if [ "$DRY_RUN" = 1 ]; then
    step "patch $mode en $file"
    return 0
  fi
  python3 - "$mode" "$file" "$payload" <<'PY'
import json, sys
mode, file, payload = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(file).read()
if mode == "tsconfig-paths":
    data = json.loads(text)
    paths = data.setdefault("compilerOptions", {}).setdefault("paths", {})
    if "@systutor/shell/*" in paths:
        print("  paths ya presentes, omito")
        sys.exit(0)
    paths["@systutor/shell/*"] = [payload + "/src/*"]
    open(file, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"  paths agregados a {file}")
elif mode == "vite-alias":
    marker = 'alias: {'
    if "@systutor/shell" in text:
        print("  alias ya presente, omito")
        sys.exit(0)
    alias_line = f'      "@systutor/shell": path.resolve(__dirname, "{payload}/src"),\n'
    idx = text.index(marker) + len(marker)
    text = text[:idx] + "\n" + alias_line + text[idx:]
    open(file, "w").write(text)
    print(f"  alias agregado a {file}")
PY
}

install_core() {
  ensure_venv
  step "kernel: pip install -e $CORE_PATH (editable)"
  if [ "$DRY_RUN" = 0 ]; then
    "$(venv_python)" -m pip install -e "$CORE_PATH"
  fi
}

install_shell() {
  local web="$1" vite_conf ts_conf rel
  vite_conf="$web/vite.config.ts"
  ts_conf="$web/tsconfig.json"
  if [ ! -f "$vite_conf" ]; then warn "$vite_conf no existe — alias no configurado"; return 0; fi
  if [ ! -f "$ts_conf" ]; then warn "$ts_conf no existe — paths no configurados"; return 0; fi
  rel="$(python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$SHELL_PATH" "$web")"
  python_patch tsconfig-paths "$ts_conf" "$rel"
  python_patch vite-alias "$vite_conf" "$rel"
  step "npm: instalando deps del frontend"
  [ "$DRY_RUN" = 1 ] && return 0
  if [ -f "$web/package-lock.json" ]; then
    (cd "$web" && npm ci --no-audit --no-fund) \
      || warn "npm ci fallo — instala manualmente deps del frontend"
  else
    (cd "$web" && npm install --no-audit --no-fund) \
      || warn "npm install fallo — instala manualmente deps del frontend"
  fi
}

main() {
  local target="."
  while [ $# -gt 0 ]; do
    case "$1" in
      --core) DO_SHELL=0; shift ;;
      --shell) DO_CORE=0; shift ;;
      --web-dir) WEB_DIR="$2"; shift 2 ;;
      --check-arch) CHECK_ONLY=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      -*) echo "opcion desconocida: $1"; usage; exit 1 ;;
      *) target="$1"; shift ;;
    esac
  done

  check_arch
  [ "$CHECK_ONLY" = 1 ] && exit 0

  cd "$target"
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: $target no es un repo git" >&2
    exit 1
  fi

  [ "$DO_CORE" = 1 ] && { add_submodule "$CORE_URL" "$CORE_PATH"; install_core; }
  [ "$DO_SHELL" = 1 ] && { add_submodule "$SHELL_URL" "$SHELL_PATH"; install_shell "$WEB_DIR"; }

  log "listo. pins: git submodule status"
  step "git submodule status"
  [ "$DRY_RUN" = 1 ] || git submodule status
}

main "$@"
