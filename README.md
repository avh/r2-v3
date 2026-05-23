# R2 Personal Assistant

A locally-hosted personal assistant server for a household Mac Mini. Each user gets a Personal Agent (PA) that holds conversation, maintains memory across sessions, and delegates specialized tasks to Tool Agents (TAs).

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Create server.env with your API keys
cp server.env.example server.env   # or create manually — see Configuration

# Start the server
./server.sh

# Open the web UI
open http://localhost:8080
```

Click **New Assistant** in the sidebar to create your first PA. The agent will introduce itself and ask for your name.

## Architecture

```
User ──WebSocket──► Personal Agent (PA)
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
           help TA    clock TA   weather TA   ...
```

**Personal Agents** maintain conversation history, short- and long-term memory, and orchestrate Tool Agents via a tag protocol embedded in the model's output stream.

**Tool Agents** are stateless handlers for specialized tasks (time, weather, web search, etc.). They are invoked by the PA using `<<Q: name\n...\n>>` tags and reply with `<<A: name\n...\n>>` tags.

See [DESIGN.md](DESIGN.md) for full design documentation.

## Configuration

`config.json` (top-level defaults):

```json
{
  "model": "omlx:Qwen3.6-35B-A3B-UD-MLX-4bit",
  "thinking": true,
  "mlx_url": "http://localhost:8000/v1/chat/completions",
  "memory_compaction_trigger_size": 4096
}
```

`server.env` (not committed — create manually):

```bash
export MLX_API_KEY=your_mlx_key
export OPENAI_API_KEY=your_openai_key
```

Config is deep-merged in this order: `config.json` → `src/agents/personal/config.json` → `user/<PA>/config.json`. Later values override earlier ones.

### Model Backends

| Prefix | Description |
|--------|-------------|
| `omlx:` | Local MLX server (OpenAI-compatible, default) |
| `openai:` | OpenAI API |
| `ollama:` | Ollama (stub, not yet implemented) |

Example: `"model": "openai:gpt-4o"` to use OpenAI instead of a local model.

## Running the Server

```bash
./server.sh                          # default port 8080
PORT=9000 ./server.sh                # custom port via env
python main.py --port 8080 --reload  # direct invocation
python main.py --base /path/to/data  # custom PA data directory
```

`--reload` enables hot-reload for development (default in `server.sh`).  
`--base` sets the directory where PA data is stored (default: `user/`).

## Web UI

The interface has a collapsible sidebar (PAs → sessions → TA sessions) and a chat area.

- **New Assistant** — creates a PA and immediately starts the first session
- **+** on a PA — adds a new session
- **×** on a PA or session (hover to reveal) — deletes with confirmation
- **◀/▶** toggle in the chat header — collapses/expands the sidebar

### Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | List all commands |
| `/think [on\|off]` | Enable or disable model thinking |
| `/show [type]` | Show bubbles of a given type |
| `/hide [type]` | Hide bubbles of a given type |
| `/status` | Show PA name, session ID, model |
| `/time` | Show last response timing (TTFT, TPS, tokens) |
| `/memory [short\|long]` | Show memory contents |
| `/prompt` | Show the full session preamble |
| `/new` | Start a fresh session (flushes notes to long-term memory) |
| `/save` | Open session transcript in a new tab |
| `/close` | Close this session and return to the session list |
| `/reset` | Restart this session (keeps memory, clears conversation) |

## Memory

**Short-term memory** (`user/<PA>/<session>/memory.txt`) — the PA appends notes during a session using `<<NOTE:` tags. Restored as a FYI at session startup after a server restart.

**Long-term memory** (`user/<PA>/memory.txt`) — shared across all sessions. Written by the PA using `<<REMEMBER:` tags for important facts, and auto-compacted when it exceeds `memory_compaction_trigger_size` bytes.

## File Structure

```
config.json          default config
system.txt           top-level system prompt
server.sh            server start script
server.env           API keys (not committed)
main.py              CLI entry point
requirements.txt
src/
  server.py          FastAPI app + REST + WebSocket
  session.py         PA/TA session lifecycle and registry
  models.py          model backend abstraction
  stream_parser.py   tag scanner (<<Q>>, <<A>>, <<NOTE>>, etc.)
  config.py          JSON deep-merge loader
  prompts.py         system prompt builder
  agents/
    personal/        PA implementation
    help/            lists available TAs
    clock/           answers date/time questions
    weather/         weather (placeholder)
user/
  <PA-name>/         one directory per personal agent
    memory.txt       long-term memory
    config.json      per-PA config overrides (optional)
    system.txt       per-PA system prompt additions (optional)
    <session-id>/    one directory per session
      memory.txt     short-term memory
      log.txt        session log
www/
  index.html
  index.css
  index.js
```

## Adding a Tool Agent

1. Create `src/agents/<name>/agent.py` with a `handle_question(question, ta_session) -> str` function.
2. Add a `system.txt` describing the agent's purpose (first line is used as the description by the `help` TA).
3. Register the import in `_dispatch_ta()` in `src/agents/personal/agent.py`.

The PA will discover the new agent via the `help` TA automatically.
