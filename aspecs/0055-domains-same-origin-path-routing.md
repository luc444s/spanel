# A.SPEC 0055 — Rutas same-origin para aplicaciones Compose

> `risk: high` — modifica configuración dinámica de Traefik. La ruta se genera
> de forma declarativa, valida contenedores/puertos y conserva rollback por
> archivo gestionado.

## WHY

Una SPA necesita alcanzar su API desde el navegador. Publicar frontend y API
en hosts distintos obliga a gestionar CORS y otro certificado. SPanel debe
poder publicar un único origen y enviar un prefijo como `/api` a otro servicio
Compose sin exponer puertos Docker en el host.

## WHAT

Extender primero el motor y CLI de Domains para que un dominio tenga:

- una ruta principal `Host(domain)` hacia el contenedor frontend;
- una ruta HTTPS de mayor prioridad para `Path(/api) || PathPrefix(/api/)`;
- un backend independiente para esa ruta, conectado a la red del proxy;
- preservación de rutas y puertos al añadir o quitar dominios del mismo sitio.

Ejemplo:

```bash
python3 scripts/proxy_domain.py add \
  --site app-frontend --site-port 80 \
  --domain app.example.com \
  --path-prefix /api --path-site app-backend --path-port 8000
```

El prefijo se conserva al reenviar. La aplicación backend decide sus rutas y
autorización; Domains sólo publica transporte HTTP. Base de datos, Redis y
otros servicios no se publican.

## VALIDATION

- tests unitarios del YAML, validación y CLI;
- comprobación de idempotencia y preservación al reescribir;
- `ruff check` sobre archivos modificados;
- prueba del laboratorio Docker cuando esté disponible.
