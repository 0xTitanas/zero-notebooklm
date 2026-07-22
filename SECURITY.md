# Security Policy

ZeroNotebookLM is a public alpha. The repository contains a working client,
package, CLI, and local session/profile storage, but it has no production or
security-audit guarantee.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.7.2 alpha | Security fixes on the latest alpha only |

## Sensitive data handling

Gemini Notebook (formerly NotebookLM) automation can involve Google session material. Do not include any of the following in issues, pull requests, logs, screenshots, prompts, fixtures, or examples:

- Google passwords
- 2FA codes
- recovery codes
- raw cookies
- OAuth tokens or refresh tokens
- browser local-storage/session-storage dumps
- private notebook IDs, account emails, source titles, or artifact contents unless sanitized

Treat `storage_state.json` and browser automation profiles as credentials. Keep them out of source control, shared backups, logs, and support bundles. The project writes its own auth files with owner-only permissions where the platform supports them, but file permissions are not a substitute for protecting the containing account and host.

Use disposable Gemini Notebook notebooks/accounts for live parity smoke tests whenever possible.

## Browser and local-port safety

Browser helpers currently support interactive login and cookie import. They are optional and are not the default HTTP/RPC runtime path. When using them:

- bind browser debugging ports to `127.0.0.1` only;
- do not expose debugging ports through reverse proxies or public tunnels;
- use dedicated browser profiles for automation;
- never automate password or 2FA entry through generic scripts;
- redact cookies, tokens, headers, RPC payloads, and page dumps before sharing evidence.

## Reporting vulnerabilities

Use **Report a vulnerability** on the repository's Security tab for private
disclosure. Do not post credentials, live exploit details, or sensitive account
material in public issues.

When reporting, include:

- affected commit or release, if any;
- minimal reproduction using sanitized fixtures where possible;
- whether Google credentials, cookies, notebook IDs, or source contents were exposed;
- whether the issue involves live Gemini Notebook behavior, local credential handling, or documentation.

## Current claims deliberately withheld

This project does not yet claim:

- production readiness;
- security audit completion;
- safe handling of arbitrary untrusted notebooks or sources;
- fully offline Gemini Notebook operation;
- stable compatibility with private Google RPCs;
- safe public exposure of any browser debugging endpoint.
