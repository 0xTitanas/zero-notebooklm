# RPC fixtures (Phase 0 skeletons)

Synthetic, sanitized batchexecute fixtures mirroring the upstream
`notebooklm-py==0.7.2` wire shape (see `wire_shape.json`). They are structurally
faithful — XSSI prefix (`)]}'`), `wrb.fr` envelope, JSON-in-string payloads — so the
request/response parser tests in `tests/fake_server/` exercise real decoding
behavior without any live NotebookLM call or private data.

## Fixture pairs

| Request | Response | Shape |
| --- | --- | --- |
| `list_notebooks.request.txt` | `list_notebooks.response.txt` | unary batchexecute (list notebooks) |
| `list_sources.request.txt` | `list_sources.response.txt` | unary batchexecute (list sources for a notebook) |
| `list_notes.request.txt` | `list_notes.response.txt` | unary batchexecute (list notes for a notebook) |
| `list_artifacts.request.txt` | `list_artifacts.response.txt` | unary batchexecute (list artifacts for a notebook) |
| `chat_ask.request.txt` | `chat_ask.streaming.response.txt` | streamed chat answer chunk |

Each `*.request.txt` is a form-encoded `f.req=...&at=...` batchexecute body; each
`*.response.txt` begins with the `)]}'` XSSI guard and carries its data as a JSON
string nested inside a `wrb.fr` row (a second JSON parse).

**No real account, cookie, notebook id, token, or source content appears in any
fixture.** All identifiers are obvious placeholders (`fake-notebook-0001`,
`SYNTHETIC_XSRF_TOKEN`, `chat-rpc`).
