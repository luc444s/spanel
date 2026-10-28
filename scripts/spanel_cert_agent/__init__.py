"""Agente privilegiado mínimo para certificados de correo (A.SPEC 0035).

Superficie deliberadamente pequeña:

- lee solicitudes de un directorio escribible por la web;
- valida hostname contra allowlist y ejecuta Certbot con argv fijo;
- instala el par en rutas estables y actualiza SNI;
- escribe `state.json` y `jobs/` solo con metadatos.

Nunca acepta argumentos, rutas, hooks ni variables de entorno desde la
solicitud. Si falta configuración de SNI o de recarga, falla cerrado.
"""

from __future__ import annotations

__version__ = "0.1.0"
