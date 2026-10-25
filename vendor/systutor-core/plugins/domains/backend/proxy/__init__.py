"""Primitivas del proxy de dominios: configuración, documentos, YAML y sondeo."""

from __future__ import annotations

from .config import LAB_ROOT, PROFILES, REPO_ROOT, SCRIPTS_DIR, Settings
from .core import (
    EXIT_CERT,
    EXIT_DNS,
    EXIT_OK,
    EXIT_TRANSPORT,
    EXIT_VALIDATION,
    EXIT_WORDPRESS,
    FILE_PREFIX,
    MARKER,
    ProxyError,
    atomic_write,
    document_container,
    document_domains,
    document_resolver,
    is_managed_document,
    normalize_domain,
    parse_site_document,
    render_site_yaml,
    router_rule,
    site_file_name,
    slug_for,
    timestamp,
    validate_managed_path,
)
from .operations import ManagedDomain, ProxyOperations
from .probe import CertificateInfo, probe_certificate
from .transport import CommandResult, Runner, build_runner

__all__ = [
    "EXIT_CERT",
    "EXIT_DNS",
    "EXIT_OK",
    "EXIT_TRANSPORT",
    "EXIT_VALIDATION",
    "EXIT_WORDPRESS",
    "FILE_PREFIX",
    "LAB_ROOT",
    "MARKER",
    "PROFILES",
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "CertificateInfo",
    "CommandResult",
    "ManagedDomain",
    "ProxyError",
    "ProxyOperations",
    "Runner",
    "Settings",
    "atomic_write",
    "build_runner",
    "document_container",
    "document_domains",
    "document_resolver",
    "is_managed_document",
    "normalize_domain",
    "parse_site_document",
    "probe_certificate",
    "render_site_yaml",
    "router_rule",
    "site_file_name",
    "slug_for",
    "timestamp",
    "validate_managed_path",
]
