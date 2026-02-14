"""SQL driver and connection errors.

All errors inherit from BaseApplicationError per project error handling rules.
"""

from postgres_fastmcp.common.errors import BaseApplicationError


class ConnectionFailedError(BaseApplicationError):
    """Database connection or pool initialization failed."""

    def __init__(self, error_details: str | None) -> None:
        """Initialize with connection error details (e.g. obfuscated message).

        Args:
            error_details: Connection error details (passwords obfuscated).
        """
        message = f"Connection attempt failed: {error_details}"
        super().__init__(message)
        self.error_details = error_details
