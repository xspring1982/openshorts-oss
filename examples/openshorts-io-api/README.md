# Calling the hosted openshorts.io API

If you don't want to deploy the runner yourself, the hosted version at
[openshorts.io](https://openshorts.io) accepts video links over an
[MCP](https://modelcontextprotocol.io) endpoint. You can use it from an AI
client such as Claude, Cursor or ChatGPT, or call it directly as JSON-RPC over
HTTP from your own code, as these examples do.

- Endpoint: `https://openshorts.io/api/mcp` (Streamable HTTP, stateless, JSON responses)
- Auth: `Authorization: Bearer os_...`. Create a key at
  <https://openshorts.io/settings/apikeys>. Calls use that account's minutes.
- Tools: `clip_video` (url, optional `caption_style`, `aspect_ratio`), `get_job`,
  `list_jobs`, `get_usage`. Call `tools/list` for the full schemas.
- Sources: one public YouTube, TikTok, Instagram or Facebook video per call.
  A job usually finishes in 10–20 minutes. Clip URLs are signed and expire,
  so download them promptly.

```bash
export OPENSHORTS_API_KEY=os_...   # keep it out of your shell history and repos
python clip_video.py "https://www.youtube.com/watch?v=..."
npx tsx clip_video.ts "https://www.youtube.com/watch?v=..."   # Node 18+
```

Both scripts submit the video, poll `get_job` every 30 seconds, and print the
clip titles and download links.

To add the server to an MCP client, use the URL above and the same header,
for example in a `mcp.json`:

```json
{
  "mcpServers": {
    "openshorts": {
      "url": "https://openshorts.io/api/mcp",
      "headers": { "Authorization": "Bearer ${OPENSHORTS_API_KEY}" }
    }
  }
}
```
