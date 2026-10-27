# Compatibility and limitations

## Environment

| Component | Requirement |
| --- | --- |
| Dify | Minimum declared version: 1.17.1 |
| Plugin runner | Python 3.12; amd64 or arm64 |
| Network | Outbound HTTPS to `api.anysearch.com` |
| Credentials | Optional AnySearch API Key; anonymous access is subject to quota |
| Search/extract Workflow example | No model required |
| Discovery Agent example | A model supporting tool calls; the supplied DSL selects `deepseek-flash` |

Local package installation depends on workspace permissions and signature
policy, including on Dify Cloud. A source-debug connection does not install a
package. Importing a DSL requires selecting the destination workspace's tools
and credentials; model credentials are separate from AnySearch credentials.

## Search and discovery

- Catalog entries, parameters and source availability can change. Use `discover`
  to inspect the current catalog; saved examples and fixtures are not a live list.
- General search, academic and legal vertical searches need a nonempty query,
  including DOI lookups. Other tags may still require terms at the upstream service.
- Directory presence and successful HTTP/business responses do not guarantee
  exact DOI matches, effective filters, complete coverage or relevant results.
- The plugin does not promise operational flight-status data. A fare or itinerary
  result must not be interpreted as a flight's live status.
- Failed vertical searches are not silently changed to general searches.

## Extraction and failures

Extraction targets public HTTP(S) pages. The upstream service determines format
support; PDF, Office, media and XHTML extraction are not guaranteed. A URL's
extension alone does not establish its format or whether extraction will succeed.

`max_chars` limits local output. `truncated` describes local truncation only;
`upstream_truncation` remains `unknown`. An output URL that falls back to the
input URL is not proof of the final redirect destination.

Zero search results and empty extraction content can be successful responses;
inspect `warnings`. Errors fail the Dify tool, so connect error branches where
appropriate. Quota exhaustion (HTTP 402) and rate limiting (HTTP 429) are failures.
Search and extraction POSTs are not automatically retried; retries may consume
additional quota even after a timeout.

See the [tool reference](tool-reference.md) for exact limits and output behavior,
the [security boundary](security.md) for trust assumptions, and the
[development guide](development.md) for reproducible local checks.
