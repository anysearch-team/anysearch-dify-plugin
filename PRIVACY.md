# Privacy notice for the AnySearch Dify plugin

This notice covers the four tools in `anysearch-team/anysearch` version `0.1.0`:
general search, capability discovery, vertical search and page extraction. A
Dify application chooses when to invoke a tool and what inputs to provide.
Queries, search filters, URLs and returned content may contain personal or
sensitive information. Only send information you are authorized to process.

## Data sent to AnySearch

The plugin connects over HTTPS only to `api.anysearch.com`:

| Tool | Data sent |
| --- | --- |
| General search | The query, requested result count and optional zone and language. |
| Vertical search | The query, result count, zone/language, selected catalog tag and supplied JSON parameters. Parameters can include DOI, year, social platform, keyword or region. Parameters that a catalog description says are ignored are still sent; the plugin cannot confirm whether each routed source uses them. |
| Page extraction | The supplied public HTTP(S) URL, including its path and query string. The plugin removes the fragment. `max_chars` is applied locally to the returned content and is not sent to AnySearch. |
| Capability discovery | Requests for public domain and tag descriptions. These requests contain selected domain names, but no search query or page URL. |

If an API Key is configured, the plugin sends it to AnySearch as a Bearer
credential for search, vertical search and extraction. Catalog discovery
requests are anonymous. Without
a Key, search, vertical search and extraction use AnySearch's anonymous access. The plugin does
not send the Key to search result sites or the page selected for extraction.

AnySearch routes search requests through its data sources and fetches extraction
URLs from its infrastructure. A target site can receive the resulting page
request; the plugin cannot determine which search sources receive a particular
query or how those sources and target sites retain data. The plugin checks URL
syntax and blocks certain local or non-public addresses before forwarding an
extraction URL. DNS resolution, redirect validation and network egress
protections are the upstream service's responsibility, outside this plugin's control.
This repository does not independently verify its deployed protections. See the
[security boundary](https://github.com/anysearch-team/anysearch-dify-plugin/blob/master/docs/security.md).
Do not put secrets in URL paths or query strings.

## Data returned to Dify and stored by the plugin

Search results and extraction titles, URLs and content are returned to Dify.
Tool outputs can also include the original search query or extraction input
URL, so Dify applications and their logs may retain these values. The plugin
does not request Dify storage permission and does not persist user queries,
results, extraction content or credentials. It caches only public catalog
descriptions in process memory for a limited time.

The plugin's own diagnostic log records the tool name, fixed API path,
duration, HTTP status or error code, sanitized request ID, HTTP call count and
catalog cache flag. It does not intentionally log queries, full URLs, page
content or API Keys. The Dify plugin SDK and hosting environment may produce
additional diagnostics, including traceback metadata on errors.

## Retention and requests about data

[AnySearch's public privacy policy](https://www.anysearch.com/legal?type=privacy)
(v1.1, checked 2026-10-08) says it retains API call logs for 12 months and
the first 100 characters of search queries as a troubleshooting summary. It
says complete **search** requests and results are not persistently stored after
the API response. It does not clearly state a retention period for extraction
URLs or extracted page content. Its [terms of service](https://www.anysearch.com/legal?type=tos)
also say User Business Data is retained for 30 days after service termination;
the relationship of that clause to the search-response rule is unclear. This
notice does not extend the search retention statement to extraction or promise
that no other service stores the data. For AnySearch data access or deletion
requests, use the process in its public policy or contact
`support@anysearch.com`.

[Dify's log documentation](https://docs.dify.ai/en/use-dify/monitor/logs)
describes Cloud logging; self-hosted Dify retention and deletion depend on the
operator. Consult the Dify workspace operator for its records. AnySearch's
policy does not govern the independent processing of routed search sources or
destination sites.
