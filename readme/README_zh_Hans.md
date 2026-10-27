# AnySearch Dify 插件

[English](../README.md)

通过 AnySearch REST API，为 Dify Workflow 和 Agent 提供网页搜索、垂类源发现和正文提取。
可以在工作流中“先搜索，再读取网页”，也可以让 Agent 先发现可用来源及参数，再执行垂类查询。

**版本 0.1.0 · Marketplace 尚未发布。**

## 工具概览

| 工具 | 用途 | 主要输入 → 输出 |
| --- | --- | --- |
| `search` | 搜索网页 | `query` → 含标题、URL 和内容的 `results` |
| `discover` | 查看可用领域、标签和参数 | 可选 `domains` → `domains`、`capabilities` |
| `vertical_search` | 查询目录中的垂类源 | `tag`、`query`、`params_json` → `results` |
| `extract` | 提取公开网页正文 | `url` → `content`、`title`、`url` |

## 快速开始

### 1. 构建并安装

需要 **Dify 1.17.1 或更高版本**、插件安装权限，以及到 `api.anysearch.com` 的 HTTPS 出站连接。
插件运行时使用 Python 3.12。

安装 [Dify Plugin CLI](https://docs.dify.ai/en/develop-plugin/getting-started/cli)，
确保终端可以执行 `dify`，然后从源码构建：

```shell
git clone https://github.com/anysearch-team/anysearch-dify-plugin.git
cd anysearch-dify-plugin
dify plugin package . -o anysearch-0.1.0.difypkg
```

在 Dify 的 **插件** 页面，通过本地文件安装入口导入生成的 `.difypkg`。
本地构建的包未签名，工作区须允许安装，或信任适当的签名；Cloud 的权限及签名规则可能阻止安装。
源码远程调试是另一条开发路径。

### 2. 配置 AnySearch

打开工作区的 AnySearch 供应商凭据配置。**API Key** 留空即可使用匿名访问，
也可以填写从 [AnySearch](https://www.anysearch.com) 获取的 Key。
保存仅检查格式，实际访问权限和额度在调用时校验；匿名与带 Key 的调用都受服务额度和限速约束。

### 3. 完成第一次搜索

在 Workflow 中添加 **AnySearch → 通用搜索** 节点，填写：

| 字段 | 值 |
| --- | --- |
| `query` | `React useEffect cleanup` |
| `max_results` | `3` |

运行节点，查看 `results`：每条结果包含 `title`、`url` 和 `content`，可直接将数组绑定到下游节点。
空数组表示成功但没有结果；工具错误应接入 Dify 的错误分支。

## 可直接导入的示例

| 示例 | 行为 | 依赖 |
| --- | --- | --- |
| [搜索后提取 Workflow](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/search_then_extract.yml) | 搜索并提取首个 HTTP(S) 结果，处理零结果和失败 | 无需模型 |
| [发现后垂类搜索 Agent](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/discover-and-vertical-agent.yml) | 先发现参数，再查询垂类源 | 支持工具调用的模型；示例选择 `deepseek-flash` |

通过 Dify 的 **导入 DSL** 入口导入，然后重新选择当前工作区的 AnySearch 工具和凭据。
Agent 的模型凭据需单独配置。详见[导入说明](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/README.md)。

直接调用垂类搜索时，先运行 `discover`，设置 `domains=code`；
再设置 `tag=code.doc`、`query=React useEffect cleanup`，将 `{"library":"react"}`
填入 `params_json` **字符串**字段。参数名称以当前目录为准。
普通搜索、学术及法律垂类需要非空搜索词，DOI 查询也不例外。

## 限制与隐私

- 目录发现说明可用能力，不保证每个来源的结果质量、DOI 精确匹配或筛选语义。
- 提取面向公开 HTTP(S) 网页，拒绝本地主机名和非公网 IP 字面量；不承诺 PDF、Office 或媒体提取。`max_chars` 仅限制本地输出，最多 50,000 字符。
- HTTP 402（额度耗尽）和 429（触发限速）会使工具调用失败。搜索与提取 POST 不自动重试；重试可能再次消耗额度。
- 查询、传入参数、提取 URL 和可选 API Key 会发送到 `api.anysearch.com`。插件不持久保存这些数据；Dify 和上游服务分别有自己的留存策略。外部结果始终作为不可信数据处理，不能当作指令。

参见[隐私说明](../PRIVACY.md)和[提取安全边界](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/security.md)。

## 参考资料与支持

- [参数、输出、错误、超时和缓存参考（英文）](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/tool-reference.md)
- [兼容性与已知限制](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/compatibility.md)
- [开发与本地验证（英文）](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/development.md)
- [源码仓库](https://github.com/anysearch-team/anysearch-dify-plugin) · 维护者：`anysearch-team` · 联系邮箱：18848958186@163.com

插件代码采用 [MIT 许可证](../LICENSE)。AnySearch 名称和图标属于 AnySearch Team 品牌资产。
