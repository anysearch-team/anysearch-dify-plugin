from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anysearch.catalog import catalog
from anysearch.errors import AnySearchError, AnySearchToolError
from anysearch.output import catalog_summary


class DiscoverTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            payload = catalog.discover(tool_parameters)
            messages = [self.create_json_message(payload)]
            messages.extend(self.create_variable_message(name, value) for name, value in payload.items())
            messages.append(self.create_text_message(catalog_summary(payload)))
        except AnySearchError as error:
            raise AnySearchToolError(str(error)) from None
        except Exception:
            raise AnySearchToolError(str(AnySearchError("INTERNAL_ERROR"))) from None
        yield from messages
