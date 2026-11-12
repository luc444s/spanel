"""Reusable PowerDNS capability for the Domains plugin.

The package deliberately keeps ``service`` out of the import graph so the
operator CLI can run inside the isolated lab container without FastAPI or
SQLAlchemy installed.
"""

from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import (
    DnsConfigError,
    DnsConflictError,
    DnsError,
    DnsNotFoundError,
    DnsPermissionError,
    DnsQueryError,
    DnsValidationError,
    PowerDnsAuth,
    PowerDnsConflict,
    PowerDnsError,
    PowerDnsNotFound,
    PowerDnsTimeout,
    PowerDnsUnavailable,
    PowerDnsValidation,
)
from plugins.domains.backend.dns.models import DnsRecord, DnsRRset, DnsZone

__all__ = [
    "DnsConfigError",
    "DnsConflictError",
    "DnsError",
    "DnsNotFoundError",
    "DnsPermissionError",
    "DnsQueryError",
    "DnsRRset",
    "DnsRecord",
    "DnsValidationError",
    "DnsZone",
    "PowerDnsAuth",
    "PowerDnsClient",
    "PowerDnsConfig",
    "PowerDnsConflict",
    "PowerDnsError",
    "PowerDnsNotFound",
    "PowerDnsTimeout",
    "PowerDnsUnavailable",
    "PowerDnsValidation",
]
