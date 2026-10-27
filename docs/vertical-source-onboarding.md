# Working with dynamic vertical sources

The plugin reads AnySearch's public catalog at runtime. It does not maintain a
static list of source tags. Agents and workflows select the source; the plugin
validates parameters, sends the request and parses the protocol response.

## Discover and call a source

1. Call `discover` with empty `domains` to list domains.
2. Call `discover` with the desired domain to inspect tags, descriptions,
   required parameters and declared machine constraints. Up to five unique,
   comma-separated domains can be requested together.
3. Choose an exact returned `tag`. Put extra parameters in `params_json` as a
   JSON object string, preserving their JSON value types. Omit unused optional
   parameters rather than sending null unless the catalog permits null.
4. Call `vertical_search` with the user's search terms and the selected tag.
   Inspect `results`, `warnings` and the returned `catalog` metadata.

For example, after discovering `code`, a `code.doc` call can use:

```json
{
  "tag": "code.doc",
  "query": "React useEffect cleanup",
  "params_json": "{\"library\":\"react\"}",
  "max_results": 3
}
```

The catalog may change; confirm the tag and parameters before using an example.
The application must supply search terms where required. The plugin does not
invent missing queries. See [query requirements](tool-reference.md#vertical-search).

## Catalog changes and failures

`refresh=true` refreshes the index and requested subdirectories. Discovery
requests remain anonymous even when the provider has a Key. A failed refresh
does not return stale cached data as a successful fallback.

Vertical search validates against the catalog even when an Agent skips an
explicit discovery call. Unknown domains, tags or parameters get a bounded
refresh; if still unknown, the call fails before a search POST. It does not
silently select a different source. Catalog metadata can help diagnose which
description and topology a call used; different scopes need not share a snapshot.

## Extending the integration

A new catalog tag usually needs no new Dify tool or hardcoded routing. Confirm
the generic validation and response parser can represent its contract. Do not
infer types or specialized filtering behavior from a natural-language description
when machine constraints are absent.

Keep three questions separate: is the tag discoverable, does the protocol call
work, and do the returned results satisfy the intended use? A successful call
alone does not prove exact matching, filtering or source reliability. Check those
properties in your application before depending on them.

For a reproducible defect in generic plugin behavior, add a focused regression
test. Source-specific semantic rules should express a confirmed constraint and
stop applying when the relevant catalog contract changes. Avoid single-source
fallbacks that hide upstream failures. See the [scope decision](adr/0001-keep-vertical-scope-at-dynamic-protocol.md)
and [development guide](development.md).
