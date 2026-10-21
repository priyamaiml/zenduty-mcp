# zenduty-mcp

MCP (Model Context Protocol) server for [Zenduty](https://www.zenduty.com): read incidents, alerts, on-call, schedules and escalation policies; acknowledge, resolve and annotate incidents behind an explicit confirmation flag.

Single-file Python server run by [uv](https://docs.astral.sh/uv/); dependencies (`mcp<2`, `httpx`) are declared inline in `server.py`.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). Clone the repo:

```bash
git clone https://github.com/priyamaiml/zenduty-mcp.git
```

Create a Zenduty API key (Account → API Keys) and expose it as `ZENDUTY_API_KEY`, either in the environment or in `~/.secrets/zenduty_env`:

```bash
mkdir -p ~/.secrets && chmod 700 ~/.secrets
echo 'export ZENDUTY_API_KEY="<key>"' > ~/.secrets/zenduty_env
chmod 600 ~/.secrets/zenduty_env
```

Register with an MCP client (omp `~/.omp/agent/mcp.json`, Claude Code, Cursor):

```json
{
  "mcpServers": {
    "zenduty": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--quiet", "--script", "/absolute/path/to/server.py"]
    }
  }
}
```

Optional: `ZENDUTY_BASE_URL` (default `https://www.zenduty.com`).

## Tools

| Kind | Tools |
|---|---|
| Read | `list_teams`, `list_services`, `list_incidents`, `get_incident`, `get_incident_alerts`, `get_incident_notes`, `get_oncall`, `list_schedules`, `list_escalation_policies` |
| Write | `acknowledge_incident`, `resolve_incident`, `add_incident_note` |

Write tools refuse unless called with `confirm=true`; agents must pass it only after the user approves the specific action.

`list_incidents` statuses: `triggered`, `acknowledged`, `resolved`, or `open` (triggered + acknowledged, two API calls since the filter accepts one status).

## Behaviour notes

- Incident listing uses `POST /api/incidents/filter/` (as the official Python SDK does). `GET /api/incidents/` answers `HTTP 209 Blocked`.
- The filter endpoint caps pages at 50 results; `list_incidents` returns one page.
- Per-incident endpoints rate-limit (HTTP 429) after roughly 30 rapid calls.
- `integration_key` and `webhook_url` fields are masked as `[redacted]` in every response.

## License

[MIT](LICENSE)
