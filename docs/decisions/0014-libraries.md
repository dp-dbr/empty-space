# ADR-0014: Supporting libraries

* Status: accepted
* Date: 2026-11-20

| Library | Why | Alternatives | Exit path |
|---|---|---|---|
| jsonschema | Draft 2020-12 validation of data files | fastjsonschema | `SchemaValidator` wrapper in adapters |
| Jinja2 | Localizable factsheet templates | Mako | Template rendering behind `SubjectType.render_factsheet` |
| deepdiff + difflib | Field-level data diffs, text diffs | jsondiff | `domain.diff` uses a `DiffEngine` port |
| argon2-cffi | Password hashing | bcrypt | `PasswordHasher` port in local IdP |
| Authlib | OAuth 2.1/OIDC client for MCP connections & future SSO | requests-oauthlib | used only in `adapters/oauth.py` |
| cryptography | Fernet for vault | PyNaCl | `CredentialVault` interface |
| pypdf, python-docx, openpyxl | Text extraction | Docling (optional) | `FileProcessor` registry |
| python-magic | MIME sniffing for upload allowlist | filetype | `MimeSniffer` function in adapters |
| OpenTelemetry SDK + OTLP | Vendor-neutral tracing | vendor agents | standard protocol |
| httpx | HTTP client (OAuth, tests) | requests | — |
| PyYAML | AgentDefinitions, SubjectType manifests | ruamel.yaml | — |
