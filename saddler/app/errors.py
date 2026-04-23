class AppError(Exception):
    """Base error for the application (UseCase) layer."""


class NotFoundError(AppError):
    """Requested resource does not exist."""


class AmbiguousIdentifierError(AppError):
    """Identifier matches multiple resources."""


class ConflictError(AppError):
    """Operation would violate an integrity constraint (e.g. resource in use)."""


class ValidationError(AppError):
    """Input is structurally or semantically invalid."""
