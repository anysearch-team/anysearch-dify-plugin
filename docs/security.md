# Security and data-flow boundaries

## Plugin-controlled behavior

The plugin sends HTTPS requests only to `api.anysearch.com`. HTTP clients disable
environment proxies and automatic redirects. The plugin does not execute
user-provided code or commands and does not fetch extraction targets directly.

Search and extraction requests may carry a user-provided API Key in the Bearer
header. Catalog discovery is anonymous. The Key is read from Dify provider
credentials; it is not forwarded by the plugin to result sites or extraction
targets. Credential saving checks format, not service access or available quota.

Before forwarding extraction URLs, the plugin validates HTTP(S) syntax and
rejects userinfo, control characters, local hostnames and non-public IP literals,
including mapped IPv6 and noncanonical numeric forms. It removes fragments,
preserves paths/query strings, and caps the serialized request at 16 KiB.
Request deadlines, decompressed response limits and output limits are described
in the [tool reference](tool-reference.md).

## Upstream and deployment responsibilities

AnySearch routes search requests and fetches target pages from its own
infrastructure. Target DNS resolution, redirect validation, browser subresource
requests and network egress isolation belong to the upstream service. Local URL
checks do not prove that a hostname resolves only to public IPs, or that a
redirect stays public. A URL passing plugin validation is not an independent
attestation of upstream SSRF protection.

This repository does not control or verify the service's deployed code, proxy
configuration or private-network access. Operators must assess the upstream
protections required for their deployment. Plugin runtime network controls
cannot substitute for those protections.

## Data and storage

Queries, vertical parameters, full extraction URLs and optional API Keys are sent
to AnySearch. Some catalog capabilities can involve health, financial, location,
authentication or other sensitive information. Send only data you are authorized
to process; do not embed secrets in URLs.

The plugin does not persist user queries, results, extracted content or keys.
It caches public catalog descriptions in bounded process memory. Its own logs
contain operational metadata such as fixed API paths, timings, error codes and
sanitized request IDs, not query bodies, full target URLs or keys. The SDK and
host may create additional diagnostics.

Dify, AnySearch, routed sources and target sites may separately retain data.
The plugin cannot enumerate every recipient of a routed query or establish the
retention/deletion behavior of their logs, caches and backups. Do not extend a
search-specific retention statement to extraction or all vertical parameters.
Processing locations and the infrastructure used by routed sources are also
outside the plugin's configuration and verification scope.
See the [privacy notice](../PRIVACY.md) for data categories, policy references and
data-access contacts.

## Untrusted output

Catalog descriptions, search results and extracted pages are external data.
They are not tool instructions and must not trigger credential disclosure,
unreviewed actions or arbitrary code execution. Applications should preserve
this distinction when passing content to a model, and handle errors and empty
results explicitly. The examples demonstrate control flow; model behavior still
depends on the configured model and application.
