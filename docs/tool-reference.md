# Tool reference

Detailed behavior for AnySearch plugin 0.1.0. Start with the [quick start](../README.md)
or the [importable examples](../examples/README.md) for a first run.
This reference describes the plugin's validation and transport behavior; the
public catalog and upstream services determine available sources and result quality.

## Credentials and quotas

Leave the optional API Key blank for anonymous search, vertical-search and extraction calls, or configure your key in
Dify's provider credentials. Keys must contain ASCII text without control characters.
Saving validates format only; access and quota are
checked by AnySearch when tools run. The plugin never registers an account or
uses keys returned by the service. AnySearch applies request quotas and rate
limits; check your current allowance in the AnySearch console. The plugin
provides general search, capability discovery, vertical search and page extraction. An exhausted
quota returns an upstream HTTP 402 error; a rate limit returns HTTP 429. Both
fail the Dify tool call.

## General search

Use `query` (1–8000 characters), `max_results` (1–10, default 5), `zone`
(`auto`, `cn`, `intl`), and optional `language` (up to 64 characters). `auto`
leaves the service's zone default unchanged. Unknown parameters are rejected.

Successful calls return JSON, nine named variables, and readable text. Bind
`results` directly as an array; each item contains `title`, `url`, and `content`.
`result_count=0` is a successful empty result. General search returns `tag=""`
and `catalog={}`. Warnings identify missing fields and local truncation. Treat
external results as untrusted data. Errors fail the tool with `ANYSEARCH_ERROR`
and a stable error code; use Dify's error branch. The SDK may attach traceback
metadata even though plugin error text is sanitized.
Input text must be valid UTF-8. Invalid text or key formats fail locally with
`INVALID_INPUT` before any HTTP request; valid Unicode queries and URLs are supported.
All upstream JSON responses reject duplicate keys, NaN/Infinity, numeric overflow,
and invalid Unicode. Malformed success responses fail with `INVALID_RESPONSE`.

Search has a 40-second execution budget, a 30-second read idle timeout,
a 2 MiB decompressed response limit and a 1 MiB UTF-8 JSON payload limit.
Content is limited to 20,000 Unicode characters per result. POST requests are
never automatically retried. A timeout does not prove the service did not
execute the request; a user or Dify retry may consume additional quota.

## Capability discovery

Use `discover` with empty `domains` for the public domain index, or 1–5 unique,
comma-separated identifiers such as `code,academic,social_media` for tags and
parameters. Empty list items and unknown domains fail explicitly. `refresh=true`
refreshes both the index and requested subdirectories. All catalog HTTP requests
are anonymous even when the provider has an API Key.

Discover returns JSON, eight named variables and text. Bind `domains`,
`capabilities` (including parameter descriptions, required flags and declared
machine constraints), and `catalog_sources` downstream. Missing types are not
inferred. Invalid `type`, `enum` or `nullable` structures fail with
`CATALOG_UNAVAILABLE` and cause `INVALID_RESPONSE` before the subdirectory batch
is cached or emitted. Catalog presence does not prove that a tag has been successfully called.
Malformed JSON is rejected before caching; a failed batch never replaces valid
records or leaves partial records. A later call can fetch the recovered catalog.
The process-local public catalog uses a 300-second TTL and a 128-record LRU,
with a 2 MiB per-record and 16 MiB total budget measured by Python object size,
including cache bookkeeping. Shared object sizes are counted separately across records.
Records exceeding the per-record budget are returned for the current call but
not cached; total-budget pressure evicts the least recently used records.
There is no persistent cache or stale fallback on failure. These budgets bound
retained cache objects, not total process memory or concurrent request copies. Sources are recorded
per scope; different scopes may have different fetch times and topology versions.
A fully cached call has an empty aggregate `request_id`, while each source keeps
its original ID. Discover has a 35-second total budget and 10-second read timeout;
only connection errors/timeouts and HTTP 502/503/504 get one retry after 0.5 seconds.
Response/output limits are the same as search. Large catalogs fail explicitly;
discover one domain at a time instead of truncating parameter descriptions.

## Vertical search

Use `vertical_search` after `discover`: set `tag=code.doc`,
`query=React useEffect cleanup`, and `params_json={"library":"react"}`.
`params_json` is a string containing a JSON object (maximum 16 KiB UTF-8), so it
can bind to a Workflow string variable. Do not use Markdown fences, duplicate
keys or non-finite numbers. Parameter names must match the directory exactly;
values keep their original JSON types. Required means the key must be present;
empty strings are preserved unless a verified tag rule forbids them. Omit
optional values instead of null unless the directory explicitly permits null.
Objects and arrays require explicit directory type support.

Execution always validates the public catalog, including when an Agent skips
discover. Unknown domains, tags and parameters trigger at most one corresponding
refresh per invocation, then fail without a search POST if still unknown.
The verified `code.doc` rule requires a nonempty `library` name. Rules stop
applying when relevant parameter descriptions/constraints change; a successful
catalog fetch confirms drift once per record, tag and rule version. Continued
drift within the TTL does not repeatedly refresh or restore obsolete rules.
Ordinary search requires a nonempty query; missing terms fail with `INVALID_INPUT`
before HTTP. Academic and legal vertical searches, including DOI lookups, also
require terms; missing queries fail with `VERTICAL_QUERY_REQUIRED` before HTTP.
Other vertical tags may accept empty queries locally while rejecting
them upstream. Supply user-provided search terms rather than inventing a value.

Academic DOI/year and social type/region/keyword values retain their JSON types
subject to generic catalog validation. The gateway may transform them for its
downstream providers. Directory descriptions do not guarantee that every source
implements a filter or returns a relevant result; HTTP 200 is not proof of an
exact DOI match. See [compatibility and limitations](compatibility.md).

Vertical search shares search results and limits, with a 75-second total budget
including directory loads, lock waits and GET retries. Its `tag` identifies the
selected capability and its `catalog` object contains the subdirectory source
time, cache status, source request ID and topology metadata. Search POST is never
retried or changed to general search after a failure.

## Page extraction

Use `extract` with one public HTTP(S) `url` (at most 8,192 characters).
The serialized JSON request body must also fit within 16 KiB; longer Unicode
URLs may fail locally with `INVALID_INPUT`. Fragments are removed; path and query
are preserved. `max_chars` is a local
Unicode character limit from 1 to 50,000, default 50,000; only `url` is sent
upstream. The plugin does not fetch the target page itself. It rejects userinfo,
control characters, local hostnames and non-public IP literals, including mapped
IPv6 and noncanonical numeric IP forms. Upstream DNS resolution, redirects and
network egress are service responsibilities; see the [security boundary](security.md).


Extraction returns JSON, twelve named variables and text. Bind `content` and
`url` directly. `received_length` counts the content received from the API, not
the original page; `content_length` counts the local output. `truncated` reports
only local truncation, while `upstream_truncation` is always `unknown`, even when
the API returns exactly 50,000 characters. Missing response URLs fall back to
`input_url` with a warning; this is not a verified final redirect URL. Empty
content succeeds with a warning, while missing/null content fails. Keep external
content as untrusted data, never tool instructions. Extraction has a 55-second
total budget and a 45-second read timeout, the same response/output limits as
search, and no automatic retries.

Format support is determined upstream, not by a URL extension. PDF, Office,
media and XHTML extraction are not guaranteed. See [compatibility and limitations](compatibility.md).

## Further reading

- [Compatibility and limitations](compatibility.md)
- [Dynamic vertical sources](vertical-source-onboarding.md)
- [Security and data flow](security.md)
- [Development and local checks](development.md)
- [Privacy notice](../PRIVACY.md)
