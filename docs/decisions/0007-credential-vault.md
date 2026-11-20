# ADR-0007: CredentialVault — Fernet-encrypted rows in the database

* Status: accepted
* Date: 2026-11-20

## Decision
Secrets (OAuth tokens, API keys) are stored in `credential_secrets` encrypted with
`cryptography.fernet.MultiFernet` (key rotation) using keys from `DP_AC_VAULT_KEYS`.
Domain code only sees opaque `CredentialRef`s. Secrets never appear in logs (redaction filter).

## Alternatives
HashiCorp Vault / Azure Key Vault / AWS Secrets Manager (later adapters).

## Exit path
`CredentialVault` interface (`put`, `get`, `delete`, `rotate`).
