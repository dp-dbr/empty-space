# ADR-0010: The Collector issues its own OAuth 2.1 access tokens for API/MCP clients

* Status: accepted
* Date: 2026-11-20

## Context
External MCP clients must act for exactly one user with read/write scopes.

## Decision
Minimal OAuth 2.1 authorization server inside the API: authorization code + PKCE (S256
mandatory), public clients registered by admins (or dynamic registration), scopes `dpac.read`
and `dpac.write`, opaque random tokens stored as sha256 hashes, refresh tokens rotated.
Login for the authorization step reuses the auth session. When an external IdP is used later,
tokens can be issued by that IdP instead and validated via JWKS (Authlib).

## Alternatives
Delegating entirely to an external IdP (not available in the MVP; would block local dev).
