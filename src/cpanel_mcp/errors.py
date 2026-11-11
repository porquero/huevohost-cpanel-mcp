"""Errores tipados del servidor.

Cada error tiene un código estable para la auditoría. Los mensajes están
pensados para mostrarse al usuario y nunca deben incluir secretos.
"""

from __future__ import annotations

from typing import Any


class CpanelMCPError(Exception):
    """Error base con un código estable para auditoría."""

    code = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details = details


class ConfigError(CpanelMCPError):
    code = "config_error"


class ValidationError(CpanelMCPError):
    code = "validation_error"


class ActionDisabledError(CpanelMCPError):
    code = "action_disabled"


class CPanelError(CpanelMCPError):
    code = "cpanel_error"


class CPanelAuthError(CPanelError):
    code = "invalid_token"


class CPanelPermissionError(CPanelError):
    code = "permission_denied"


class CPanelTimeoutError(CPanelError):
    code = "timeout"


class CPanelConnectionError(CPanelError):
    code = "connection_error"


class CPanelProtocolError(CPanelError):
    code = "protocol_error"


class CPanelAPIError(CPanelError):
    code = "cpanel_api_error"


class AccountExistsError(CpanelMCPError):
    code = "account_exists"


class DomainNotHostedError(CpanelMCPError):
    code = "domain_not_hosted"


class SubdomainExistsError(CpanelMCPError):
    code = "subdomain_exists"


class MailRoleDisabledError(CPanelError):
    code = "mail_role_disabled"


class FileAlreadyExistsError(CpanelMCPError):
    code = "file_exists"


class FileOperationError(CpanelMCPError):
    code = "file_operation_failed"


class RemoteFileNotFoundError(CpanelMCPError):
    code = "file_not_found"


class UnsafePathError(CpanelMCPError):
    code = "unsafe_path"


class FileTooLargeError(CpanelMCPError):
    code = "file_too_large"


class FileTypeNotAllowedError(CpanelMCPError):
    code = "file_type_not_allowed"


class SymlinkError(CpanelMCPError):
    code = "symlink_rejected"


class FileStorageRoleDisabledError(CPanelError):
    code = "file_storage_role_disabled"
