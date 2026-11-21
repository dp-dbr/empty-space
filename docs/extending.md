# Extending the DP Agentic Collector

The core knows no concrete subject types, channels or data sources. Everything domain-specific is
added through the 13 extension points (see [ARCHITECTURE.md §5](ARCHITECTURE.md#5-extension-points)).
No extension requires core code changes.

## How plugins are loaded

1. **Folder plugins** in any directory listed in `DP_AC_PLUGINS_DIRS` (default `backend/plugins/`):
   * a folder with `subject_type.yaml` is loaded as a SubjectType package;
   * a folder with `plugin.py` must expose `register(registries)`.
2. **Python packages** exposing the entry point group `dp_agentic_collector.plugins`:

   ```toml
   [project.entry-points."dp_agentic_collector.plugins"]
   my_plugin = "my_package.plugin:register"
   ```

`register(registries)` receives the `Registries` object; register by name, e.g.
`registries.channels.register("teams", TeamsChannel())`. Every interface carries
`INTERFACE_VERSION`; registering an implementation of a different version fails fast.
Infrastructure points (stores, vault, queue, identity, LLM) register **factories**
`lambda app: Impl(...)`; the active one is selected by configuration (`DP_AC_ARTIFACT_STORE`,
`DP_AC_ATTACHMENT_STORE`, `DP_AC_CREDENTIAL_VAULT`, `DP_AC_TASK_QUEUE`, `DP_AC_IDENTITY_PROVIDER`,
`DP_AC_PROJECTION`) or per AgentDefinition (LLM). `GET /api/v1/plugins` and `dpac plugins` list
what is loaded.

Rules for all extensions: never import other tenants' data (every call carries an
`ActorContext` with `tenant_id`), never log secrets, and keep third-party types inside your plugin.

## SubjectType (folder package)

```
plugins/subject_types/server/
  subject_type.yaml        # name, schema_version, title, files below
  schema.json              # JSON Schema (draft 2020-12) of the data file
  factsheet.en.md.j2       # Jinja2 (sandboxed) per locale: de, fr, it, en
  guide.en.md              # conversation guide per locale (fed to the collector agent)
  rules.py                 # RULES = [ValidationRule subclasses], CONSOLIDATION_CHECKS = [callables]
  migrations.py            # optional: MIGRATIONS = [SchemaMigration("1", "2", fn)]
```

`subject_type.yaml`:

```yaml
name: server
schema_version: "1"
title: {en: Server, de: Server}
schema: schema.json
rules: rules.py
templates: {en: factsheet.en.md.j2, de: factsheet.de.md.j2}
guides: {en: guide.en.md, de: guide.de.md}
```

Templates receive `data` (the data file) and `ctx` with `subject`, `changes` (since previous
version), `previous_version`, `sources`, `open_points`, `rationale`. Structured facts belong in
the data file only; templates render tables from it.

A `ValidationRule` implements `check(data, ctx) -> list[RuleViolation]`; `ctx.peer_data` holds the
latest data of the other subjects of the Cycle (keyed by `lineage_key`). Consolidation checks take
`{lineage_key: data}` and return `ConsolidationIssue`s (kind `gap` or `contradiction`).

Breaking schema changes: bump `schema_version` and register a `SchemaMigration` so older
versions stay readable. See `backend/plugins/subject_types/application` for a complete example.

## ChannelAdapter

```python
from dp_agentic_collector.extensions.interfaces import ChannelAdapter, OutboundMessage

class EmailChannel(ChannelAdapter):
    name = "email"
    def send(self, message: OutboundMessage) -> None: ...   # deliver notification / link to the job
    def receive(self): return []                            # optional pull of inbound replies

def register(r): r.channels.register("email", EmailChannel())
```

Jobs choose their channel (`channel` on CollectionJob). Inbound replies must be posted through
`ConversationService.post_user_message` in the context of the authenticated user.

## DataSourceConnector

Prefer an **MCP server** (out-of-process, any language, per-user credentials) — see next section.
In-process connectors are for trusted sources:

```python
class CmdbConnector(DataSourceConnector):
    name = "cmdb"
    def tools(self, ctx):  # ToolSpec list; classification "read" or "write"
        return [ToolSpec("cmdb_lookup", "[read] Look up in the CMDB", {"type": "object", "properties": {"q": {"type": "string"}}})]
    def call(self, ctx, tool, arguments):
        facts = ...  # query with ctx.actor's rights only
        return ToolOutput({"facts": facts}, sources=({"kind": "cmdb", "on_behalf_of": ctx.actor.user_id,
                                                      "retrieved_at": "...", "reference": "..."},))
```

Add the tool name (or a glob) to `allowed_tools` of the AgentDefinition. Write tools are never
executed directly: the runtime turns them into confirmation requests.

## MCP server (no code)

Admin → *MCP servers* (or `POST /api/v1/mcp-servers`):

| Field | Meaning |
|---|---|
| `transport` | `streamable_http` (URL) or `stdio` (command line) |
| `auth_type` | `none`, `api_key` (user enters key; header via `oauth.api_key_header`), `oauth2` (`oauth.authorize_url`, `token_url`, `client_id`, optional `client_secret`, `scope`, `resource`) |
| `max_access_mode` | `read` or `read_write` — upper bound for user grants |
| `tool_classification` | overrides `{tool: read|write}`; otherwise `readOnlyHint`, unknown = write |

Users connect their own accounts under *My connections*; tokens are stored in the
CredentialVault and refreshed automatically. Agents see tools as `mcp__<server>__<tool>`.

## Identity provider

`IdentityProvider.authenticate(tenant_id, credentials) -> AuthenticatedIdentity`. The bundled
`oidc` provider (Authlib) works with any compliant IdP: set `DP_AC_IDENTITY_PROVIDER=oidc`,
`DP_AC_OIDC_ISSUER`, `DP_AC_OIDC_CLIENT_ID`, `DP_AC_OIDC_CLIENT_SECRET`; users are matched by
`ExternalIdentity(provider="oidc", subject=<sub>)`.

## LLM provider

```python
class MyProvider(LlmProvider):
    name = "my_provider"
    def complete(self, *, model, system, messages, tools, max_tokens=4096, temperature=None) -> LlmResponse:
        ...  # translate LlmMessage/LlmToolSpec to the vendor API and back

def register(r): r.llm_providers.register("my_provider", lambda app: MyProvider())
```

Select it per AgentDefinition (`llm.provider`, `llm.model`), per tenant (Admin → LLM provider,
stored in tenant settings `llm.default` or `llm.<agent name>`), or globally
(`DP_AC_DEFAULT_LLM_PROVIDER`). Add the provider to the contract test
`tests/contract/test_llm_adapters.py` with a recording fake client. Opaque provider data that must
be replayed unchanged (e.g. thinking blocks) goes into `LlmResponse.raw`.

## AgentDefinition

YAML in `DP_AC_AGENTS_DIR` (overrides built-ins by name):

```yaml
api_version: dpac/v1
name: collector
role: collector
llm: {provider: anthropic, model: claude-opus-5-5}
allowed_tools: [get_draft, propose_facts, read_attachment, "mcp__asset_registry__*"]
max_steps: 8
escalation: {notify: cycle_owner}
system_prompt: |
  ...
```

Cycles reference an AgentDefinition by name (`agent_definition`).

## File processor

```python
class PptxProcessor(FileProcessor):
    name = "pptx"
    mime_types = ("application/vnd.openxmlformats-officedocument.presentationml.presentation",)
    def extract(self, content, filename, mime_type) -> ExtractedContent: ...
```

Also add the MIME type to `DP_AC_ATTACHMENT_MIME_ALLOWLIST`. (Docling can be wrapped the same way.)

## Storage backends

* **ArtifactStore** — implement the `ArtifactStore` interface (atomic pair `put` with optimistic
  locking, `get` by version/latest/snapshot, `history`, `diff`, `as_of`, `verify`, snapshots) and
  run `tests/contract/test_artifact_store.py` against it by adding it to the fixture params.
* **AttachmentStore** — content-addressed `put/get/exists/erase` per tenant, e.g. S3-compatible
  object storage with keys `<tenant_id>/<sha256>`.
* **CredentialVault** — `put/get/replace/delete` returning opaque references (HashiCorp Vault,
  Azure Key Vault, AWS Secrets Manager).
* **TaskQueue** — `register/run_pending/start/stop`; forward outbox rows to an external queue.
* **Projection** — `project/query/history` into any query store.

Register as factories and select via `DP_AC_*` settings.
