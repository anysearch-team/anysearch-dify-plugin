# Dify examples / Dify 示例

These DSL files contain no workspace credentials. Install the AnySearch plugin
in the destination workspace before importing them. Select that workspace's
AnySearch provider and credentials after import. An empty API Key uses anonymous
access; configure your own Key in Dify if needed. Do not place keys in app inputs.

## Search, then extract Workflow

Import [search_then_extract.yml](search_then_extract.yml) through **Create → Import
DSL**. Select the local AnySearch provider in both tool nodes, then run it with
a nonempty query. The Workflow searches for three results and sends the first
structured HTTP(S) URL to extraction. It sends content, source URL, title,
lengths, truncation status and warnings to End. It needs no model.

Zero results and a missing HTTP(S) URL have separate messages and do not invoke
extract. Tool failures follow error branches. Treat returned page content as
untrusted data. A URL can pass the Workflow check and still fail the plugin's
stricter validation.

## Discover, then vertical search Agent

Import [discover-and-vertical-agent.yml](discover-and-vertical-agent.yml) as an
Agent. The DSL selects `deepseek-flash` with function calling and at most six
iterations. Configure a tool-capable model and its credential in the target
workspace, or replace the model and verify its behavior there. Model credentials
and AnySearch credentials are separate; neither is stored in the DSL.

Select the workspace's `discover` and `vertical_search` tools after import.
The Agent should call discovery before searching and pass `params_json` as a
JSON object **string**. It requests at most three results. Academic and legal
tags need a nonempty query, including DOI lookup. An empty query fails with
`VERTICAL_QUERY_REQUIRED` before HTTP. If search terms are missing, the Agent
should ask the user. It must stop after any tool error and must not
treat external content as instructions. Confirm the actual tool trace in the
destination workspace; model behavior can vary.

Read [compatibility and limitations](../docs/compatibility.md) before deploying
the examples in your workspace.

## 中文说明

两个 DSL 均不包含工作区凭据。先在目标工作区安装插件；导入后选择该工作区的
AnySearch 工具和凭据。API Key 留空可使用匿名访问，不要把 Key 写进应用输入。

- [搜索后提取 Workflow](search_then_extract.yml)：通过 **创建 → 导入 DSL** 导入，
  重新选择 search 和 extract 工具，输入非空查询词即可运行，无需模型。
  零结果、无 HTTP(S) URL 和工具失败各有独立处理路径。
- [发现后垂类 Agent](discover-and-vertical-agent.yml)：目标工作区需配置支持
  工具调用的模型及模型凭据，再选择 discover、vertical_search 工具。
  `params_json` 必须是 JSON 对象字符串；学术、法律垂类及 DOI 查询需要非空 query。
  缺词时先向用户询问，工具报错后停止，不执行外部内容中的指令。

请在目标工作区检查实际调用轨迹，部署前阅读[兼容性与已知限制](../docs/compatibility.md)。
