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
- Testing: `mcp.shared.memory.create_connected_server_and_client_session(server, raise_exceptions=True)` gives an in-memory client session. It is async, so run it under anyio's pytest plugin (`@pytest.mark.anyio`).
- Strict mypy: expect an occasional `untyped-decorator` complaint on the tool decorator.
- Dependencies: pydantic>=2.12, jsonschema, starlette, uvicorn, sse-starlette, pyjwt[crypto], opentelemetry-api. HTTP deps ship even for stdio-only use. v2 dropped pydantic-settings, so it doesn't conflict with ours.
- Register with Claude Code: `claude mcp add --transport stdio pantry --scope project -- uv run python -m meals.mcp_tools`.
