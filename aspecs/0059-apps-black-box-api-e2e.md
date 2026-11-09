# A.SPEC 0059 — E2E black-box de Apps por API real

> `risk: high` — crea, despliega y elimina una aplicación real mediante producción.

## WHY

Los mocks de Runner, Docker o FastAPI no validan login, permisos, serialización,
idempotencia, cola, worker, Compose ni lifecycle como un sistema completo. Apps
necesita una prueba externa que sólo conozca su API pública.

## WHAT

- Login real por `POST /api/v1/auth/login` y validación con `/auth/me`.
- Resolución de la instalación y repositorio GitHub reales mediante API.
- Creación de una aplicación temporal con rama y Compose de Software para
  Editores.
- Inspect, deployment y polling de operaciones terminales por API.
- Validación del inventario backend/frontend/database.
- Ejecución API de refresh, logs, restart, stop y start.
- Eliminación final por API con purga de datos, incluso tras un fallo anterior.
- Contraseña sólo mediante archivo regular `0600`; token, contraseña y secretos
  generados nunca se imprimen ni se escriben en reportes.
- HTTPS obligatorio salvo loopback para un lab local. No se accede directamente
  a PostgreSQL, Redis o Docker.

## VALIDATION

La única evidencia E2E válida es la salida de una ejecución contra el panel real.
Las pruebas unitarias no sustituyen esta ejecución y no deben presentarse como
resultado E2E.
