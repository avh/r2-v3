"""Personal Agent — model loop, memory, TA routing, system commands."""

import asyncio
import shutil
import time
import zoneinfo
from datetime import datetime
from pathlib import Path

from src.models import get_backend
from src.prompts import build_system_prompt
from src.stream_parser import StreamParser
from src.session import PASession, registry

ROOT = Path(__file__).parent.parent.parent.parent
AGENTS_DIR = ROOT / "src" / "agents"

_SYSTEM_COMMANDS = {"/think", "/status", "/time", "/memory", "/new", "/save", "/help", "/show", "/hide", "/prompt", "/close", "/reset"}

_SHOWABLE_ROLES = {"think", "note", "remember", "fyi", "question", "answer", "system"}

_HELP_TEXT = """\
Available commands:

| Command | Description |
|---|---|
| `/help` | Show this message |
| `/think [on\|off]` | Enable or disable model thinking |
| `/show [type]` | Show a bubble type, or list visibility status |
| `/hide [type]` | Hide a bubble type, or list visibility status |
| `/status` | Show PA name, session ID, and current model |
| `/time` | Show timing stats for the last response |
| `/memory [short\|long]` | Display short-term or long-term memory |
| `/prompt` | Show the full session preamble (system prompt + injected context) |
| `/new` | Start a new session |
| `/save` | Open session transcript in a new tab |
| `/close` | Close this session and return to the session list |
| `/reset` | Restart this session (keeps memory, clears conversation) |

Bubble types: """ + ", ".join(f"`{r}`" for r in sorted(_SHOWABLE_ROLES))




def _build_system_prompt(pa_name: str) -> str:
    return build_system_prompt(
        AGENTS_DIR / "personal" / "system.txt",
        ROOT / "user" / pa_name / "system.txt",
    )


async def _dispatch_ta(session: PASession, agent_name: str, question: str) -> str:
    """Call the appropriate TA and return its answer string."""
    try:
        if agent_name == "help":
            from src.agents.help.agent import handle_question
        elif agent_name == "clock":
            from src.agents.clock.agent import handle_question
        elif agent_name == "weather":
            from src.agents.weather.agent import handle_question
        else:
            return f"Unknown agent: {agent_name}"
        ta_session = session.get_ta_session(agent_name)
        return await handle_question(question, ta_session)
    except Exception as e:
        return f"Error from {agent_name}: {e}"


async def _handle_system_command(session: PASession, text: str) -> bool:
    """Handle /commands. Returns True if it was a system command."""
    parts = text.strip().split()
    cmd = parts[0].lower()
    if not cmd.startswith("/"):
        return False

    if cmd not in _SYSTEM_COMMANDS:
        await session.send({"type": "message", "role": "error",
                            "text": f"Unknown command: `{cmd}`. Type `/help` for a list of commands."})
        return True

    if cmd == "/help":
        await session.send({"type": "message", "role": "system", "text": _HELP_TEXT})

    elif cmd == "/think":
        arg = parts[1].lower() if len(parts) > 1 else "on"
        session.config["thinking"] = (arg == "on")
        await session.send({"type": "message", "role": "system",
                            "text": f"Thinking {'enabled' if session.config['thinking'] else 'disabled'}."})

    elif cmd in ("/show", "/hide"):
        want_hide = (cmd == "/hide")
        if len(parts) > 1:
            role = parts[1].lower()
            if role not in _SHOWABLE_ROLES:
                await session.send({"type": "message", "role": "error",
                                    "text": f"Unknown type: `{role}`. Valid types: {', '.join(sorted(_SHOWABLE_ROLES))}"})
                return True
            hidden: set = session.config.setdefault("hidden_roles", set())
            if want_hide:
                hidden.add(role)
            else:
                hidden.discard(role)
            await session.send({"type": "visibility", "role": role, "hidden": want_hide})
            await session.send({"type": "message", "role": "system",
                                "text": f"{'Hiding' if want_hide else 'Showing'} `{role}` bubbles."})
        else:
            hidden = session.config.get("hidden_roles", set())
            rows = "\n".join(
                f"| `{r}` | {'hidden' if r in hidden else 'shown'} |"
                for r in sorted(_SHOWABLE_ROLES)
            )
            await session.send({"type": "message", "role": "system",
                                "text": f"Bubble visibility:\n\n| Type | Status |\n|---|---|\n{rows}"})

    elif cmd == "/status":
        model = session.config.get("model", "unknown")
        await session.send({"type": "message", "role": "system",
                            "text": f"PA: {session.pa_name}  Session: {session.session_id}  Model: {model}"})

    elif cmd == "/time":
        if session.last_duration is None:
            await session.send({"type": "message", "role": "system", "text": "No timing data yet."})
        else:
            ttft = f"{session.last_ttft:.2f}s" if session.last_ttft else "n/a"
            tps = f"{session.last_tps:.1f}" if session.last_tps else "n/a"
            await session.send({"type": "message", "role": "system",
                                "text": f"TTFT: {ttft}  TPS: {tps}  Tokens: {session.last_tokens}  Total: {session.last_duration:.2f}s"})

    elif cmd == "/memory":
        arg = parts[1].lower() if len(parts) > 1 else "short"
        if arg == "short":
            mem = session.read_short_memory() or "(empty)"
            await session.send({"type": "message", "role": "system", "text": f"Short-term memory:\n\n{mem}"})
        else:
            mem = session.read_long_memory() or "(empty)"
            await session.send({"type": "message", "role": "system", "text": f"Long-term memory:\n\n{mem}"})

    elif cmd == "/prompt":
        system_prompt = _build_system_prompt(session.pa_name)
        parts = [f"**System Prompt**\n\n```\n{system_prompt}\n```"]
        for msg in session.history:
            content = msg.get("content", "")
            if msg.get("role") == "user" and content.startswith("<<FYI:"):
                body = content[len("<<FYI:"):].strip().removesuffix(">>").strip()
                parts.append(f"**Context (FYI)**\n\n```\n{body}\n```")
            else:
                break
        await session.send({"type": "message", "role": "system",
                            "text": "\n\n---\n\n".join(parts)})

    elif cmd == "/new":
        short_mem = session.read_short_memory()
        if short_mem.strip():
            existing_long = session.read_long_memory()
            session.write_long_memory((existing_long + "\n" + short_mem).strip())
            session.short_memory_path.write_text("")
        await session.send({"type": "new_session"})

    elif cmd == "/save":
        transcript = _build_transcript(session)
        await session.send({"type": "transcript", "html": transcript})

    elif cmd == "/close":
        await session.send({"type": "close_session"})
        return "exit"

    elif cmd == "/reset":
        session.history.clear()
        session.msg_log.clear()
        session.stream_acc.clear()
        await session.send({"type": "reset_session"})
        return "exit"

    return True


def _build_transcript(session: PASession) -> str:
    lines = [f"<h2>Transcript: {session.pa_name} / {session.session_id}</h2><hr>"]
    for msg in session.history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        lines.append(f"<p><strong>{role}</strong><br><pre>{content}</pre></p>")
    return "\n".join(lines)


async def _call_model(session: PASession, backend, system_prompt: str):
    """Stream one model turn, handle all events, update history and timing."""
    messages = [{"role": "system", "content": system_prompt}] + session.history
    parser = StreamParser()
    accumulated_text = ""
    accumulated_think = ""
    streaming_started = False
    start_time = time.monotonic()
    first_token_time: float | None = None
    token_count = 0

    try:
        async for chunk in backend.stream_chat(messages, session.config):
            if first_token_time is None:
                first_token_time = time.monotonic() - start_time
            events = parser.parse(chunk)
            for event in events:
                await _handle_event(session, event)
                if event[0] == "text":
                    accumulated_text += event[1]
                    if event[1].strip():
                        streaming_started = True
                    if streaming_started and event[1]:
                        token_count += len(event[1].split())
                        await session.send({"type": "message", "role": "assistant",
                                            "text": event[1], "partial": True})
                elif event[0] == "think":
                    accumulated_think += event[1]
                    if session.config.get("thinking", True):
                        await session.send({"type": "message", "role": "think",
                                            "text": event[1], "partial": True})

        for event in parser.flush():
            await _handle_event(session, event)
            if event[0] == "text":
                accumulated_text += event[1]
                await session.send({"type": "message", "role": "assistant",
                                    "text": event[1], "partial": True})

    except Exception as e:
        session.log("error", str(e))
        await session.send({"type": "message", "role": "error", "text": str(e)})
        return

    duration = time.monotonic() - start_time
    session.last_ttft = first_token_time
    session.last_tokens = token_count
    session.last_duration = duration
    session.last_tps = token_count / duration if duration > 0 else None

    if accumulated_think:
        session.log("think", accumulated_think)
        await session.send({"type": "message", "role": "think", "text": "", "partial": False})
    await session.send({"type": "message", "role": "assistant", "text": "", "partial": False})

    if accumulated_text.strip():
        session.history.append({"role": "assistant", "content": accumulated_text})
        session.log("assistant", accumulated_text)

    await _maybe_compact_memory(session, backend, system_prompt)


async def run_pa_session(session: PASession):
    """Main loop for a PA session. Runs as an asyncio task."""
    system_prompt = _build_system_prompt(session.pa_name)

    # Clean up TA sessions from any previous run of this session
    session.close_ta_sessions()
    if session.session_dir.exists():
        for sub in list(session.session_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith("."):
                shutil.rmtree(sub, ignore_errors=True)

    # Determine session status
    short_mem = session.read_short_memory()
    pa_dir = session.session_dir.parent
    try:
        prior_sessions = [
            d for d in pa_dir.iterdir()
            if d.is_dir() and d.name != session.session_id and not d.name.startswith(".")
        ]
    except Exception:
        prior_sessions = []

    if not prior_sessions:
        status = ("This is your very first session with this user. "
                  "Introduce yourself by name, ask for the user's name, and ask how you can help.")
    elif short_mem:
        status = "This is a continuation of a previous conversation (the server was restarted)."
    else:
        status = "This is a new session. You have spoken with this user in previous sessions."

    # Inject system FYI: time + session status
    try:
        tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    dt_text = f"The time is {now.strftime('%A, %B %-d %Y  %I:%M %p %Z').strip()}"
    system_fyi = f"{dt_text}\n{status}"
    session.history.append({"role": "user", "content": f"<<FYI: system\n{system_fyi}\n>>"})
    session.log("fyi", f"[system]\n{system_fyi}")
    await session.send({"type": "message", "role": "fyi", "name": "system", "text": system_fyi})

    # Inject long-term memory as FYI at session start
    long_mem = session.read_long_memory()
    if long_mem:
        session.history.append({"role": "user", "content": f"<<FYI: long-term memory\n{long_mem}\n>>"})
        session.log("fyi", f"[long-term memory]\n{long_mem}")
        await session.send({"type": "message", "role": "fyi", "name": "long-term memory", "text": long_mem})

    # Restore this session's short-term memory as FYI
    if short_mem:
        session.history.append({"role": "user", "content": f"<<FYI: recent memory\n{short_mem}\n>>"})
        session.log("fyi", f"[recent memory]\n{short_mem}")
        await session.send({"type": "message", "role": "fyi", "name": "recent memory", "text": short_mem})

    backend = get_backend(session.config.get("model", "openai:gpt-4o-mini"))

    orig_thinking = session.config.get("thinking", False)
    session.config["thinking"] = False
    await _call_model(session, backend, system_prompt)
    session.config["thinking"] = orig_thinking

    while True:
        item = await session.queue.get()
        msg_type = item.get("type")

        if msg_type == "message":
            user_text = item.get("text", "").strip()
            if not user_text:
                continue

            if user_text.startswith("/"):
                result = await _handle_system_command(session, user_text)
                if result == "exit":
                    return
                if result:
                    continue

            session.log("user", user_text)
            session.history.append({"role": "user", "content": user_text})
            await _call_model(session, backend, system_prompt)

        elif msg_type == "ta_answer":
            agent_name = item.get("name", "")
            answer = item.get("text", "")
            ta_msg = f"<<A: {agent_name}\n{answer}\n>>"
            session.history.append({"role": "user", "content": ta_msg})
            session.log("answer", f"{agent_name}: {answer}")
            if answer.strip():
                await session.send({"type": "message", "role": "answer",
                                    "name": agent_name, "text": answer})
            await _call_model(session, backend, system_prompt)


async def _broadcast_memory_update(session: PASession, body: str):
    """Push a memory-update FYI to every other active session for this PA."""
    fyi_content = f"<<FYI: memory update\n{body}\n>>"
    for other in list(registry._sessions.get(session.pa_name, {}).values()):
        if other.session_id == session.session_id:
            continue
        other.history.append({"role": "user", "content": fyi_content})
        other.log("fyi", f"[memory update]\n{body}")
        await other.send({"type": "message", "role": "fyi", "name": "memory update", "text": body})


async def _handle_event(session: PASession, event: tuple):
    """Handle tag events during streaming."""
    if event[0] == "tag":
        _, tag, name, body = event
        if tag == "NOTE":
            session.append_short_memory(body)
            session.log("note", body)
            await session.send({"type": "message", "role": "note", "text": body})
        elif tag == "REMEMBER":
            existing = session.read_long_memory()
            session.write_long_memory((existing + "\n" + body).strip())
            session.log("remember", body)
            await session.send({"type": "message", "role": "remember", "text": body})
            await _broadcast_memory_update(session, body)
        elif tag == "FYI":
            session.log("fyi", f"[{name}]\n{body}" if name else body)
            await session.send({"type": "message", "role": "fyi", "name": name, "text": body})
        elif tag == "Q":
            session.log("question", f"{name}: {body}")
            await session.send({"type": "message", "role": "question", "name": name, "text": body})
            ts = session.get_ta_session(name)
            await _send_ta_sessions(session)
            await session.send({"type": "ta_message", "ta_session_id": ts.session_id,
                               "agent_name": name, "role": "question", "text": body})
            asyncio.create_task(_dispatch_and_reply(session, name, body))


async def _send_ta_sessions(session: PASession):
    await session.send({"type": "ta_sessions", "sessions": [
        {"agent_name": n, "ta_session_id": ts.session_id, "status": ts.status}
        for n, ts in session.ta_sessions.items()
    ]})


async def handle_ta_direct(session: PASession, agent_name: str, question: str):
    """Dispatch a question straight to a TA without touching PA history."""
    ta_session = session.get_ta_session(agent_name)
    ta_session.status = "pending"
    await _send_ta_sessions(session)
    try:
        await _dispatch_ta(session, agent_name, question)
    finally:
        ta_session.status = "done"
        await _send_ta_sessions(session)


async def _dispatch_and_reply(session: PASession, agent_name: str, question: str):
    """Dispatch question to TA and push answer into the PA's queue."""
    answer = await _dispatch_ta(session, agent_name, question)
    ta = session.ta_sessions.get(agent_name)
    if ta:
        ta.status = "done"
    await _send_ta_sessions(session)
    await session.queue.put({"type": "ta_answer", "name": agent_name, "text": answer})


async def _maybe_compact_memory(session: PASession, backend, system_prompt: str):
    trigger = session.config.get("memory_compaction_trigger_size", 4096)
    short_mem = session.read_short_memory()
    if len(short_mem) < trigger:
        return

    summary_messages = [
        {"role": "system", "content": "You are a memory summarizer. Given a list of session notes, produce a concise, factual summary suitable for long-term memory. Output only the summary, no preamble."},
        {"role": "user", "content": f"Session notes:\n{short_mem}"},
    ]
    summary = ""
    try:
        async for chunk in backend.stream_chat(summary_messages, session.config):
            if not chunk.startswith("\x00THINK\x00"):
                summary += chunk
    except Exception:
        return

    existing_long = session.read_long_memory()
    updated = (existing_long + "\n" + summary).strip()
    session.write_long_memory(updated)
    session.short_memory_path.write_text("")
    await session.send({"type": "message", "role": "system",
                        "text": "Memory compacted: short-term notes moved to long-term memory."})
