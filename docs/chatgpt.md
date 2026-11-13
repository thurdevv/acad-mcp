# Using acad-mcp from ChatGPT (not fully verified)

ChatGPT only connects to **remote** MCP servers, so it cannot start a program on your PC by itself. The option
that avoids exposing your PC is OpenAI's **Secure MCP Tunnel**: a small `tunnel-client` on your PC opens an
outbound connection to OpenAI; no inbound port, no public URL.

> Not verified end-to-end. Which ChatGPT plans can add custom MCP servers with write actions was not stated in
> the OpenAI pages we read; check your account first. Commands below follow OpenAI's docs as read on 2026-11-13.

1. **Check your plan**: at `https://chatgpt.com/plugins`, see whether **+ → Add custom MCP server** exists.
2. **OpenAI Platform**: create a tunnel in *Settings → Organization → Tunnels*
   (`https://platform.openai.com/settings/organization/tunnels`) and an API key for `tunnel-client`.
3. **tunnel-client**: download from `https://github.com/openai/tunnel-client/releases/latest`.
4. **Connect it to acad-mcp** (PowerShell):
   ```powershell
   $env:CONTROL_PLANE_API_KEY = "sk-..."
   .\tunnel-client.exe init --sample sample_mcp_stdio_local --profile acad --tunnel-id tunnel_YOUR_ID --mcp-command "uvx acad-mcp"
   .\tunnel-client.exe doctor --profile acad --explain
   .\tunnel-client.exe run --profile acad
   ```
5. **In ChatGPT**: `chatgpt.com/plugins` → **+** → **Add custom MCP server** → Connection **Tunnel** → choose the
   tunnel → **Create as a plugin**. ChatGPT asks for confirmation on every write tool.

Sources: OpenAI developer docs — *Connect from ChatGPT*, *Custom MCP server*, *Secure MCP Tunnels*.
