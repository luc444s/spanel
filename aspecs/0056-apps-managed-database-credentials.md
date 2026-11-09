# A.SPEC 0056 — Credenciales de datos administradas por Apps

> `risk: high` — genera y persiste material secreto utilizado por deployments.

## WHY

Una aplicación Compose puede tener cualquier cantidad de servicios y motores de
datos. Pedir al operador que coordine manualmente nombre, usuario y contraseña
entre cada servicio causa errores y expone secretos innecesariamente.

## WHAT

- Apps genera una vez por aplicación `APP_DB_NAME`, `APP_DB_USER` y
  `APP_DB_PASSWORD`.
- Nombre y usuario son identificadores neutrales y estables; la contraseña usa
  256 bits aleatorios.
- El sobre AES-256-GCM queda ligado a tenant, aplicación y versión de clave.
- Apps antepone esas variables al entorno temporal de cada deployment y rechaza
  sobrescrituras del usuario.
- Apps no asume PostgreSQL, MySQL, MongoDB, driver, host, puerto ni URL. Cada
  Compose mapea las variables neutrales a su motor y puede construir su propia
  URL mediante interpolación.
- Un purge explícito elimina también el sobre cifrado.
- UI y CLI nunca devuelven los valores administrados.

Ejemplo universal:

```yaml
services:
  database:
    environment:
      ENGINE_DATABASE: ${APP_DB_NAME}
      ENGINE_USER: ${APP_DB_USER}
      ENGINE_PASSWORD: ${APP_DB_PASSWORD}
  backend:
    environment:
      DATABASE_URL: protocol://${APP_DB_USER}:${APP_DB_PASSWORD}@database:1234/${APP_DB_NAME}
```

## VALIDATION

- migración aditiva e idempotente;
- generación estable y distinta entre aplicaciones;
- cifrado sin texto plano en base de datos;
- rechazo de sobrescrituras y limpieza con purge;
- merge antes de variables proporcionadas por UI/CLI;
- tests backend, CLI y frontend.
