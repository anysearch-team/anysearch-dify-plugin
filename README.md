# AnySearch for Dify

[Chinese (Simplified)](readme/README_zh_Hans.md)

Search the web, discover specialized search sources, and extract page content
from Dify Workflows and Agents through the AnySearch REST API. Use it to search
and read a page in one workflow, or let an Agent discover a source before querying it.

**Version 0.1.0 · Marketplace publication pending.**

## Tools

| Tool | Use it to | Main inputs → outputs |
| --- | --- | --- |
| `search` | Search the web | `query` → `results` with title, URL and content |
| `discover` | Inspect available domains, tags and parameters | Optional `domains` → `domains`, `capabilities` |
| `vertical_search` | Query a source from the catalog | `tag`, `query`, `params_json` → `results` |
| `extract` | Read a public web page | `url` → `content`, `title`, `url` |

## Quick start

### 1. Build and install

You need Dify **1.17.1 or later**, permission to install plugins, and outbound
HTTPS access to `api.anysearch.com`. The plugin runtime uses Python 3.12.

Install the [Dify Plugin CLI](https://docs.dify.ai/en/develop-plugin/getting-started/cli)
and make the `dify` command available, then build from source:

```shell
git clone https://github.com/anysearch-team/anysearch-dify-plugin.git
cd anysearch-dify-plugin
dify plugin package . -o anysearch-0.1.0.difypkg
```

In Dify's **Plugins** screen, install the generated `.difypkg` using the local-file
installation option. A locally built package is unsigned; the workspace must
permit it or trust an appropriate signature. Cloud permissions and signature
rules may prevent installation. Remote debugging is a separate development path.

### 2. Configure AnySearch

Open the AnySearch provider credentials in your workspace. Leave **API Key**
blank for anonymous access, or use your key from [AnySearch](https://www.anysearch.com).
Saving credentials checks their format; access and quota are checked when a tool runs.
Both anonymous and keyed calls are subject to the service's quotas and rate limits.

### 3. Run your first search

Add **AnySearch → General Search** to a Workflow and set:

| Field | Value |
| --- | --- |
| `query` | `React useEffect cleanup` |
| `max_results` | `3` |

Run the node and inspect `results`: each item has `title`, `url` and `content`.
Bind the array directly to downstream nodes. An empty array is a successful
zero-result response; tool errors should use Dify's error branch.

## Ready-to-import examples

| Example | What it does | Requirements |
| --- | --- | --- |
| [Search, then extract](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/search_then_extract.yml) | Searches and extracts the first HTTP(S) result; handles empty results and failures | No model needed |
| [Discover, then vertical search](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/discover-and-vertical-agent.yml) | An Agent discovers parameters before querying a specialized source | A tool-capable model; the example selects `deepseek-flash` |

Import through Dify's **Import DSL** action, then select the workspace's AnySearch
tools and credentials. For the Agent, configure model credentials separately.
See the [import guide](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/examples/README.md).

For a direct vertical-search call, first run `discover` with `domains=code`.
Then use `tag=code.doc`, `query=React useEffect cleanup`, and put
`{"library":"react"}` into the `params_json` **string** field. Use the current
catalog's parameter names. Ordinary search, academic and legal queries require
nonempty search terms, including DOI lookups.

## Limits and privacy

- Discovery describes available capabilities; it does not guarantee every source's result quality, exact DOI matching or filter semantics.
- Extraction is for public HTTP(S) pages. Local hostnames and non-public IP literals are rejected; PDF, Office and media extraction are not promised. `max_chars` limits local output to at most 50,000 characters.
- HTTP 402 (quota exhausted) and 429 (rate limited) fail the tool. Search and extraction POST requests are not automatically retried; retries may consume additional quota.
- Queries, supplied parameters, extraction URLs and optional API Keys go to `api.anysearch.com`. The plugin does not persist them; Dify and upstream services have separate retention policies. Treat returned content as untrusted data, never instructions.

Read the [privacy notice](PRIVACY.md) and
[extraction security boundary](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/security.md).

## Reference and support

- [Parameters, outputs, errors, timeouts and caching](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/tool-reference.md)
- [Compatibility and known limits](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/compatibility.md)
- [Development and local checks](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/development.md)
- [Source repository](https://github.com/anysearch-team/anysearch-dify-plugin) · Maintainer: `anysearch-team` · Contact: 18848958186@163.com

Plugin code is [MIT licensed](LICENSE). The AnySearch name and logo remain
AnySearch Team brand assets.
