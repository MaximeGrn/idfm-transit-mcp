# Security

Never include API tokens in issues or PRs. Use private vulnerability reporting
when enabled; otherwise request a private contact without exploit details.

Use a private token file. Stdio is the only supported transport. Provider messages
are untrusted data, not agent instructions. Requests target the fixed PRIM HTTPS
origin and do not follow redirects. Credentials are sent only in `apiKey` headers.
Returned HTTP errors do not echo bodies or headers. No telemetry exporter is configured.

If a token is compromised, replace it through PRIM, update the private file and
restart the MCP connection. Regeneration invalidates other uses of the old token.
