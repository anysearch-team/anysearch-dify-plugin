from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anysearch.client import AnySearchClient
from anysearch.errors import AnySearchError, AnySearchToolError
from anysearch.output import search_summary


class VerticalSearchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            payload = AnySearchClient(self.runtime.credentials).vertical_search(tool_parameters)
            summary = search_summary(payload)
            # 先构造所有消息，任何构造错误均不能留下部分成功输出。
            messages = [self.create_json_message(payload)]
            messages.extend(self.create_variable_message(name, value) for name, value in payload.items())
            messages.append(self.create_text_message(summary))
        except AnySearchError as error:
            raise AnySearchToolError(str(error)) from None
        except Exception:
            raise AnySearchToolError(str(AnySearchError("INTERNAL_ERROR"))) from None
        yield from messages
