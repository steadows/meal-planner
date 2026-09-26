---
type: research
topic: Python MCP server library (for meals/mcp_tools.py)
by: pantry
date: 2026-09-26
---
**Answer: the official `mcp` package, v2 (`MCPServer`).** A research agent reported this on 2026-09-26. **Verify the import path against the installed wheel before relying on it.**
- `mcp` 2.0.0 shipped 2026-07-28 for the 2026-07-28 MCP spec, and 2.2.0 came out 2026-09-07. It is a breaking rewrite: **`FastMCP` was renamed to `MCPServer`, and `mcp.server.fastmcp` was removed** (`from mcp.server import MCPServer`). Anyone who needs to stay on v1 can use the 1.x line (1.30.0).
- The standalone `fastmcp` package (PrefectHQ, 4.0.x, releases almost daily) is much heavier: provider extras, tasks, apps. That is too much for a small local stdio tool module.
- `@mcp.tool()` functions that return pydantic models get `outputSchema` and result validation for free. `mcp.run()` uses stdio by default.
- **Testing (CORRECTED 2026-09-26, verified against the installed 2.2.0 wheel by [[pantry]]):** `mcp.shared.memory.create_connected_server_and_client_session` does NOT exist in 2.2. Use `from mcp import Client`, then `async with Client(server) as c: await c.call_tool(name, args)`, which runs in-process. `anyio.run(...)` inside a sync test works without the pytest plugin.
- **Verified 2.2 behaviour worth knowing:**
  - `ToolError` is in `mcp.server.mcpserver.exceptions` (not exported from `mcp.server`), and reaches the client as `is_error=True` with its message.
  - Any exception that isn't a ToolError is opaque to the client: it sees only "Error executing tool <name>".
  - Tool arguments are validated by pydantic before the call.
  - **Sync tools run in a worker thread**, so a `sqlite3` connection opened on the main thread (the default `check_same_thread=True`) fails inside a tool. Open a connection per call instead.
  - A model return gives structured output, wrapped as `{"result": ...}` for `X | None` returns.
- Strict mypy: expect an occasional `untyped-decorator` complaint on the tool decorator.
- Dependencies: pydantic>=2.12, jsonschema, starlette, uvicorn, sse-starlette, pyjwt[crypto], opentelemetry-api. HTTP deps ship even for stdio-only use. v2 dropped pydantic-settings, so it doesn't conflict with ours.
- Register with Claude Code: `claude mcp add --transport stdio pantry --scope project -- uv run python -m meals.mcp_tools`.
