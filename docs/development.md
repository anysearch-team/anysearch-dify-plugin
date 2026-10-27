# Development

## Set up

Use Python 3.12 and Git. Install the
[Dify Plugin CLI](https://docs.dify.ai/en/develop-plugin/getting-started/cli)
if you want to build packages or run the CLI packaging tests.

From the repository root, create a virtual environment:

```shell
python -m venv .venv
```

Activate it with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell, or
`source .venv/bin/activate` in a POSIX shell. Then install dependencies:

```shell
python -m pip install -r requirements.txt -r requirements-dev.txt
```

## Run checks

```shell
python -m pytest -q
python -m mypy
python scripts/check_sdk.py
```

Tests use synthetic HTTP responses and do not require an AnySearch API Key or a
Dify workspace. The SDK check starts the real plugin SDK with a synthetic HTTP
fixture; it does not install a plugin in Dify or exercise the live API.

The two CLI packaging cases run when `dify` is on `PATH` or `DIFY_CLI` points to
the official executable; otherwise they are skipped. To select the CLI explicitly:

```powershell
# Windows PowerShell
$env:DIFY_CLI = (Get-Command dify).Source
python -m pytest -q tests/test_packaging.py
```

```sh
# POSIX shell
export DIFY_CLI="$(command -v dify)"
python -m pytest -q tests/test_packaging.py
```

The Windows junction case is Windows-specific. Saved catalog fixtures cover
protocol behavior, not the current availability or semantic quality of live sources.

## Build a package

```shell
dify plugin package . -o anysearch-0.1.0.difypkg
python scripts/check_release_metadata.py anysearch-0.1.0.difypkg
```

For a clean Git worktree, the release checker also verifies packaged files
against the current commit and exercises the unpacked SDK:

```powershell
# Windows PowerShell
python scripts/release_check.py --cli (Get-Command dify).Source
```

```sh
# POSIX shell
python scripts/release_check.py --cli "$(command -v dify)"
```

The release checker and SDK probe write outputs under the ignored `.codex/`
directory by default; the manual build above writes its package in the repository
root. These are local build outputs. `.difyignore` controls package contents;
`.gitignore` controls source tracking. The package file contract is maintained
in `scripts/check_package.py`.

Before distributing a package, validate it with the
[Marketplace Toolkit](https://github.com/langgenius/dify-marketplace-toolkit)
and test installation and tool use in the target Dify environment. Local checks
do not establish Marketplace approval or installation compatibility.

## Code map

| Path | Responsibility |
| --- | --- |
| `provider/` | Provider metadata and credential format validation |
| `tools/` | Dify tool definitions and output messages |
| `anysearch/validation.py` | Input and catalog-parameter validation |
| `anysearch/catalog.py` | Dynamic discovery, refresh and bounded caching |
| `anysearch/client.py`, `response.py` | Fixed-host HTTP requests, deadlines and response parsing |
| `anysearch/output.py`, `models.py`, `errors.py` | Output contracts and sanitized errors |
| `anysearch/semantic_rules.py` | Narrow rules tied to catalog contracts |
| `tests/` | Regression tests and synthetic fixtures |
| `examples/` | Importable Workflow and Agent DSLs |

The plugin discovers and validates catalog entries; Agents and workflows choose
which source to call. A successful protocol response does not establish that
upstream results satisfy a specialized semantic promise. See the
[scope decision](adr/0001-keep-vertical-scope-at-dynamic-protocol.md).

When extending behavior, keep tool YAML, input validation and output contracts
aligned. Add regression coverage for changed behavior. Avoid source-specific
fallbacks or static copies of the dynamic catalog; follow the
[vertical-source guide](vertical-source-onboarding.md). Do not commit credentials,
workspace exports containing secrets, or personal diagnostic traces.
