"""Normalized DNS errors for the client, the service and the operator CLI.

The messages in this module never include the PowerDNS administrative URL or
the API key. Callers only see a stable category and a human explanation.
"""

from __future__ import annotations


class DnsError(Exception):
    """Base error for the DNS capability."""

    status_code = 500
    code = "domains.dns.error"
    exit_code = 6


class DnsConfigError(DnsError):
    status_code = 503
    code = "domains.dns.configuration"
    exit_code = 2


class DnsValidationError(DnsError):
    status_code = 422
    code = "domains.dns.validation"
    exit_code = 2


class DnsNotFoundError(DnsError):
    status_code = 404
    code = "domains.dns.not_found"
    exit_code = 6


class DnsConflictError(DnsError):
    status_code = 409
    code = "domains.dns.conflict"
    exit_code = 4


class DnsPermissionError(DnsError):
    status_code = 403
    code = "domains.dns.permission"
    exit_code = 6


class PowerDnsError(DnsError):
    status_code = 503
    code = "domains.dns.unavailable"
    exit_code = 5


class PowerDnsTimeout(PowerDnsError):
    status_code = 504
    code = "domains.dns.timeout"


class PowerDnsUnavailable(PowerDnsError):
    status_code = 503
    code = "domains.dns.unavailable"


class PowerDnsConflict(PowerDnsError):
    status_code = 409
    code = "domains.dns.conflict"
    exit_code = 4


class PowerDnsNotFound(PowerDnsError):
    status_code = 404
    code = "domains.dns.powerdns_not_found"
    exit_code = 6


class PowerDnsValidation(PowerDnsError):
    status_code = 422
    code = "domains.dns.powerdns_validation"
    exit_code = 2


class PowerDnsAuth(PowerDnsError):
    status_code = 503
    code = "domains.dns.powerdns_auth"
    exit_code = 5


class DnsQueryError(DnsError):
    status_code = 503
    code = "domains.dns.query_failed"
    exit_code = 5
