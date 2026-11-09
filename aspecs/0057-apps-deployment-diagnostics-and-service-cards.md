# A.SPEC 0057 — Diagnóstico de deployment y tarjetas de servicios

> `risk: medium` — mejora preflight y observabilidad sin revelar valores de entorno.

## WHY

Un deployment no debe llegar a build/up para descubrir interpolaciones Compose
ausentes, ni responder sólo `command failed`. Tras un deployment sano, el
operador necesita reconocer los servicios efectivos como en un panel de
orquestación, sin exponer secretos.

## WHAT

- Antes del build, `docker compose config` detecta variables referenciadas sin
  valor y devuelve sus nombres en un `AppDeployError` seguro.
- Build y up reportan la fase y código de salida; un up parcial siempre intenta
  `down --remove-orphans`.
- El manifiesto guarda por servicio: nombre, imagen y nombres de variables
  declaradas; nunca valores.
- El detalle de Apps presenta una tarjeta por contenedor del deployment activo,
  con estado del deployment, imagen, fecha y nombres de variables.
- Manifiestos anteriores siguen respondiendo con inventario vacío compatible.

## VALIDATION

- preflight aborta antes del build y ordena nombres faltantes;
- inventario omite valores canario;
- API serializa tarjetas de servicios;
- tests/backend/frontend/build, Ruff y Pyright.
