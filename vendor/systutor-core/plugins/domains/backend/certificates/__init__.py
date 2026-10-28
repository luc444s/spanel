"""Gestor de certificados TLS de correo (A.SPEC 0035).

El paquete solo manipula metadatos y rutas. La emisión, la instalación y las
recargas viven en `scripts/spanel_cert_agent/`; la web escribe solicitudes y
lee el estado que ese agente publica.
"""

from __future__ import annotations

from plugins.domains.backend.certificates.config import CertificatesConfig
from plugins.domains.backend.certificates.inventory import Inventory, load_inventory
from plugins.domains.backend.certificates.jobs import ActiveJobError, JobStore
from plugins.domains.backend.certificates.service import CertificatesService

__all__ = [
    "ActiveJobError",
    "CertificatesConfig",
    "CertificatesService",
    "Inventory",
    "JobStore",
    "load_inventory",
]
