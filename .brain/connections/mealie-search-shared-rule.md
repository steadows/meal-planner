---
type: connection
features: [bot, mealie, search]
kind: shared-rule
status: watch
severity: high
blocks: []
files: [meals/mealie_client.py, meals/planner.py, meals/bot/]
discovered: 2026-09-26T04:40:00Z
resolved: null
updated: 2026-09-26T05:28:06Z
---
Two rules agreed between [[search]] and [[mealie]] (live SendMessage, 2026-09-26). [[mealie]] owns both.

1. **Rotation-pool tag is `rotation`.** It marks the favorites the Saturday planner may offer again (PLAN: "a mix of new finds and favorites from the rotation pool"). planner calls `list_by_tag("rotation")`. It sits in mealie's tag conventions alongside the slot tags `protein`, `grain`, `veg-tray`, `sauce`, `kid-cook` and `lunch-build`. Renaming it is a change to both lanes.

2. **SSRF guard lives at the sink, in `HttpMealieClient.import_url`.** URLs reaching import_url come from Claude's web-search output: `/find` → "save N", composed at [[bot]] as `mealie.import_url(option.url)`; search itself makes no import call, and Mealie fetches them server-side from inside the home network. So a prompt-injected page could aim Mealie at LAN or loopback hosts. The rule (revised 2026-09-26 after research): http/https only, no userinfo, no `localhost` or single-label hosts, and IP literals must be global and not reserved (loopback, private, link-local, ULA, multicast, reserved incl. NAT64/IPv4-compatible IPv6, CGNAT/Tailscale and IPv4-mapped forms rejected), and local-use names (`.localhost`, `.local`, `.internal`, `.home.arpa`) are refused. **No client-side DNS lookup:** Mealie v3.28.0's own `AsyncSafeTransport` resolves once, blocks the same ranges, pins the connection (closing the DNS-rebind gap; shipped in v3.26.0 via mealie-recipes/mealie#7914) and re-checks every redirect hop, verified in `mealie/pkgs/safehttp/transport.py`. So Mealie is the primary control, and a client-side lookup would only be a TOCTOU duplicate. The client guard is defence in depth plus a fast, clear error. Compose pins Mealie ≥ v3.28.0. A rejection raises `ValueError`, which is already the Protocol's documented failure, so no contract change. It is not a contracts URL type: a check that resolves DNS doesn't belong in a pydantic validator, and `RecipeOption.url` also carries Mealie's stored orgURL. search adds **no** second check (no half-guards). Callers (today: [[bot]]) treat ValueError as a user-facing "couldn't save that one" and do not crash; search is relaying this to bot. `FakeMealieClient` does not enforce the guard.
   Residual risk: a future Mealie regression in its guard (see `.brain/research/mealie-v3-api.md` §9 for its CVE history). Network-level egress rules on the Mealie container would be the next layer if ever wanted. High-risk TDD tier (security): RED goes through test-writer + spec-watchdog.
