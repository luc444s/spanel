"""CLI de sitios estáticos de SPanel.

Este paquete es deliberadamente independiente del plugin `hosting`: sirve para
probar el ciclo completo (volumen + nginx + contenido + Traefik + HTTPS) antes
de integrarlo a la API. La única pieza que reutiliza es `proxy_domain.py`, que
ya es la autoridad sobre el file provider y los certificados.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
