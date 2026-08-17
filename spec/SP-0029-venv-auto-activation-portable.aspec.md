# A.SPEC SP-0029 — Portable venv + auto-activation

## WHY

Hoy el entorno Python depende de pasos manuales y de `python3` en PATH.
Además, un `.venv` copiado entre máquinas no es portable porque queda
atado a rutas absolutas. Spanel necesita bootstrap reproducible en Linux
x86_64, Linux ARM64 y Termux, manteniendo una sola venv en raíz del repo.

## WHAT

Transición observable: clone limpio de Spanel ejecuta bootstrap portátil,
crea/actualiza `.venv` en raíz, instala `vendor/systutor-core` como
dependencia editable, y ofrece auto-activación opt-in al entrar al repo.
Los scripts Python usan esa venv; Vite sigue usando Node fuera de la venv.

## SCOPE

### Bootstrap raíz

- Centralizar entorno Python en `/.venv` del repo; no crear venv dentro de
  `vendor/systutor-core`.
- Extender `install.sh` para:
  - crear `.venv` si no existe.
  - actualizar `pip` base dentro de `.venv`.
  - instalar `vendor/systutor-core` en editable dentro de esa venv.
  - instalar dependencias Python adicionales de Spanel en esa misma venv.
  - mantener chequeos actuales de arquitectura (`--check-arch`) para Linux
    x86_64, ARM64 y Termux.
- Mantener `vendor/systutor-core` como submodule/dependencia; no mover su
  ownership del entorno al submodule.

### Scripts de ejecución

- `package.json` scripts Python (`services`, `services:no-reload`,
  `services-host:0.0.0.0`) deben preferir `./.venv/bin/python3` cuando
  exista, con fallback razonable a `python3` si la venv aún no fue creada.
- Scripts Node/Vite (`frontend`) no deben depender de la venv.

### Auto-activación opt-in

- Agregar helper repo-local para shells POSIX (`bash`/`zsh`/Termux shell)
  que el usuario pueda sourcear desde su rc (`~/.bashrc`, `~/.zshrc`).
- Comportamiento esperado:
  - entrar en directorio del repo → activa `.venv` si existe.
  - salir del repo → desactiva sólo si esa activación vino del hook.
- Instalación del hook debe ser explícita/documentada; esta spec no modifica
  rc global del usuario automáticamente.

### Documentación

- README explica diferencia de responsabilidades:
  - `.venv` raíz = Python / FastAPI / plugins
  - `vendor/systutor-core` = dependencia editable/submodule
  - `apps/web` = Node/Vite, no usa venv
- Documentar que Termux no necesita Docker local para desarrollo de Spanel;
  Docker sigue remoto via SSH/Tailscale.

## OUT OF SCOPE

- Soporte Windows nativo.
- Copiar `.venv` binaria entre máquinas como estrategia oficial.
- Direnv, Nix, Conda, Poetry, uv o Docker local obligatorio.
- Auto-instalar Node o Docker en Termux.

## CONTRACT

- PRE: `python3` disponible; `git` y `npm` presentes cuando corresponda al
  flujo elegido.
- POST: bootstrap root-only reproducible; `.venv` queda en raíz; scripts de
  API pueden ejecutarse sin activar manualmente si `.venv` existe.
- Auto-activación es opt-in y portable; no rompe shells fuera del repo.
- Termux debe poder bootstrapear entorno Spanel sin asumir Docker local.

## INVARIANTS

```yaml
invariants:
  - `.venv` MUST vivir solo en raiz del repo.
  - `vendor/systutor-core` MUST permanecer como dependencia/submodule, no como dueño de la venv.
  - Scripts Vite/Node MUST permanecer fuera de la venv.
  - Solucion MUST apuntar a Linux x86_64, Linux ARM64 y Termux.
  - Kernel MUST NOT ser modificado salvo instalacion editable desde raiz.
```

## VERIFICATION

```bash
bash install.sh --check-arch
bash install.sh
./.venv/bin/python3 -V
npm run services
npm run frontend
# shell nueva con hook sourceado: entrar al repo activa .venv automaticamente
```

## ROLLBACK

Revertir cambios en `install.sh`, `package.json`, scripts de hook y README.
Borrar `.venv` recreada si se quiere volver a bootstrap manual previo.

## Change Surface

```yaml
change_surface:
  allowed:
    - install.sh
    - package.json
    - README.md
    - .gitignore
    - scripts/**
  prohibited:
    - vendor/**
    - apps/web/package.json
    - docker-compose.yml
    - plugins/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - local.bootstrap.python
    - local.shell.autoactivation
    - npm.python_scripts
  indirect:
    - termux.dev.setup
  must_not_affect:
    - remote.docker.workflow
    - apps.web.node_runtime
    - vendor.systutor_core.source
```

## Traceability

- Requirement: README.md (setup local actual), install.sh, preferencia usuario de portabilidad Linux x86_64 + ARM64 + Termux.
- Commit: (pending)
- Deployment: `bash install.sh`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
