# A.SPEC 0058 — E2E real de Apps con Software para Editores

> `risk: high` — crea y elimina recursos Docker aislados en una computadora real.

## WHY

Las pruebas unitarias no detectan fallos de imágenes base, BuildKit, Compose,
healthchecks, migraciones ni conectividad entre servicios. Se necesita simular
que Apps recibe el repositorio real de Software para Editores y obtener un
diagnóstico por comando sin revelar secretos.

## WHAT

- Un script clona la rama configurada y usa el mismo auditor/deployer de Apps.
- Genera credenciales de base de datos y secretos efímeros, siempre en archivos
  `0600`; incluye `VITE_API_BASE_URL=/api` y ejecuta migraciones.
- Construye y levanta backend, frontend y database con un proyecto Docker único.
- Ejecuta las rutas lifecycle reales de refresh/status, logs y restart; vuelve a
  esperar estado running/healthy después del reinicio.
- Comprueba health, contenido frontend y conectividad frontend→backend.
- Cada comando genera un log redactado y un reporte JSON seguro.
- El bloque `finally` elimina contenedores, redes, volúmenes e imágenes del E2E
  y falla si detecta recursos etiquetados residuales.
- No publica puertos ni dominios y no toca aplicaciones o datos existentes.

## VALIDATION

- Tests verifican contrato de entorno, permisos, redacción y nombres de imagen.
- Ruff, Pyright y suite runtime/backend.
- Ejecución real dentro de `apps-worker` en una computadora con Docker.
