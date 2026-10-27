from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from anysearch.errors import AnySearchError
from anysearch.validation import credential_key


class AnysearchProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            credential_key(credentials)
        except AnySearchError:
            raise ToolProviderCredentialValidationError("API key must be ASCII text without control characters.") from None
