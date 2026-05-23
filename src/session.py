import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Awaitable


ROOT = Path(__file__).parent.parent


def _base_dir() -> Path:
    val = os.environ.get("R2_BASE_DIR")
    return Path(val) if val else ROOT / "user"


@dataclass
class TASession:
    pa_name: str
    pa_session_id: str
    agent_name: str
    pa_session: "PASession"
    session_id: str = field(default="")
    created_at: float = field(default_factory=time.time)

    status: str = field(default="pending", init=False)   # "pending" | "done"
    _answer_event: asyncio.Event | None = field(default=None, init=False, repr=False)
    _user_answer: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if not self.session_id:
            self.session_id = f"{self.agent_name}-{uuid.uuid4().hex[:8]}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session_dir(self) -> Path:
        return _base_dir() / self.pa_name / self.pa_session_id / self.session_id

    def log(self, role: str, text: str):
        with open(self.session_dir / "log.txt", "a") as f:
            f.write(f"[{role}]\n{text}\n\n")

    async def send(self, msg: dict):
        """Forward a message to the parent PA session's WebSocket."""
        await self.pa_session.send(msg)

    async def wait_for_input(self, question: str) -> str:
        """Send ta_input_needed to the client and suspend until the user replies."""
        if self._answer_event is None:
            self._answer_event = asyncio.Event()
        self._answer_event.clear()
        self._user_answer = None
        await self.send({
            "type": "ta_input_needed",
            "agent_name": self.agent_name,
            "ta_session_id": self.session_id,
            "question": question,
        })
        await self._answer_event.wait()
        return self._user_answer or ""

    def set_answer(self, text: str):
        """Called by the server when the user submits a ta_input message."""
        self._user_answer = text
        if self._answer_event is not None:
            self._answer_event.set()


@dataclass
class PASession:
    pa_name: str
    session_id: str
    config: dict
    send_fn: Callable[[dict], Awaitable[None]]  # raw WS callback; use send() to send

    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    ta_sessions: dict[str, TASession] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = field(default=None, init=False)

    # Replay log: finalised messages suitable for replaying to a newly connected client
    msg_log: list[dict] = field(default_factory=list, repr=False)
    # Accumulator for in-progress streaming messages keyed by role
    stream_acc: dict = field(default_factory=dict, repr=False, init=False)

    # timing for /time command
    last_ttft: float | None = field(default=None, init=False)
    last_tps: float | None = field(default=None, init=False)
    last_tokens: int = field(default=0, init=False)
    last_duration: float | None = field(default=None, init=False)

    async def send(self, msg: dict) -> None:
        """Send msg to the client and record it in msg_log for later replay."""
        msg_type = msg.get("type")
        role = msg.get("role")
        if msg_type == "message":
            if msg.get("partial") is True:
                # Accumulate streaming chunks; don't log yet
                if role not in self.stream_acc:
                    entry: dict = {"type": "message", "role": role, "text": ""}
                    if "name" in msg:
                        entry["name"] = msg["name"]
                    self.stream_acc[role] = entry
                self.stream_acc[role]["text"] += msg.get("text", "")
            elif msg.get("partial") is False:
                # Stream ended — commit the accumulated message if it has content
                entry = self.stream_acc.pop(role, None)
                if entry and entry["text"].strip():
                    self.msg_log.append(entry)
            else:
                # Non-streaming message (note, fyi, error, system, …)
                self.msg_log.append(msg)
        elif msg_type in ("visibility", "ta_sessions"):
            self.msg_log.append(msg)
        elif msg_type == "ta_message":
            ta_partial = msg.get("partial")
            ta_sid = msg.get("ta_session_id", "")
            ta_role = msg.get("role", "")
            acc_key = f"ta_{ta_sid}_{ta_role}"
            if ta_partial is True:
                if acc_key not in self.stream_acc:
                    self.stream_acc[acc_key] = {
                        "type": "ta_message", "ta_session_id": ta_sid,
                        "agent_name": msg.get("agent_name", ""), "role": ta_role, "text": "",
                    }
                self.stream_acc[acc_key]["text"] += msg.get("text", "")
            elif ta_partial is False:
                entry = self.stream_acc.pop(acc_key, None)
                if entry and entry["text"].strip():
                    self.msg_log.append(entry)
            else:
                self.msg_log.append(msg)
        await self.send_fn(msg)

    @property
    def session_dir(self) -> Path:
        return _base_dir() / self.pa_name / self.session_id

    @property
    def short_memory_path(self) -> Path:
        return self.session_dir / "memory.txt"

    @property
    def log_path(self) -> Path:
        return self.session_dir / "log.txt"

    @property
    def state_path(self) -> Path:
        return self.session_dir / "state.json"

    def ensure_dirs(self):
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def read_short_memory(self) -> str:
        if self.short_memory_path.exists():
            return self.short_memory_path.read_text()
        return ""

    def append_short_memory(self, note: str):
        self.ensure_dirs()
        with open(self.short_memory_path, "a") as f:
            f.write(note.strip() + "\n")

    def read_long_memory(self) -> str:
        path = _base_dir() / self.pa_name / "memory.txt"
        if path.exists():
            return path.read_text()
        return ""

    def write_long_memory(self, content: str):
        path = _base_dir() / self.pa_name / "memory.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n")

    def log(self, role: str, text: str):
        self.ensure_dirs()
        with open(self.log_path, "a") as f:
            f.write(f"[{role}]\n{text}\n\n")

    def save_state(self, extra: dict | None = None):
        self.ensure_dirs()
        state = {"session_id": self.session_id, "pa_name": self.pa_name}
        if extra:
            state.update(extra)
        self.state_path.write_text(json.dumps(state, indent=2))

    def get_ta_session(self, agent_name: str) -> TASession:
        if agent_name not in self.ta_sessions:
            self.ta_sessions[agent_name] = TASession(
                pa_name=self.pa_name,
                pa_session_id=self.session_id,
                agent_name=agent_name,
                pa_session=self,
            )
        return self.ta_sessions[agent_name]

    def close_ta_sessions(self):
        self.ta_sessions.clear()

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "title": self.session_id}


class SessionRegistry:
    def __init__(self):
        # pa_name -> {session_id -> PASession}
        self._sessions: dict[str, dict[str, PASession]] = {}

    def get_or_create(
        self,
        pa_name: str,
        session_id: str,
        config: dict,
        send: Callable[[dict], Awaitable[None]],
    ) -> tuple[PASession, bool]:
        pa_sessions = self._sessions.setdefault(pa_name, {})
        created = False
        if session_id not in pa_sessions:
            session = PASession(pa_name=pa_name, session_id=session_id, config=config, send_fn=send)
            session.ensure_dirs()
            pa_sessions[session_id] = session
            created = True
        else:
            # Update the send callback when the client reconnects
            pa_sessions[session_id].send_fn = send
        return pa_sessions[session_id], created

    def get(self, pa_name: str, session_id: str) -> PASession | None:
        return self._sessions.get(pa_name, {}).get(session_id)

    def list_sessions(self, pa_name: str) -> list[dict]:
        return [s.to_dict() for s in self._sessions.get(pa_name, {}).values()]

    def close(self, pa_name: str, session_id: str):
        pa_sessions = self._sessions.get(pa_name, {})
        session = pa_sessions.pop(session_id, None)
        if session:
            session.close_ta_sessions()
            if session.task and not session.task.done():
                session.task.cancel()

    def close_pa(self, pa_name: str):
        pa_sessions = self._sessions.pop(pa_name, {})
        for session in pa_sessions.values():
            session.close_ta_sessions()
            if session.task and not session.task.done():
                session.task.cancel()

    def list_pa_names(self) -> list[str]:
        pa_dir = _base_dir()
        if not pa_dir.exists():
            return []
        return [d.name for d in pa_dir.iterdir() if d.is_dir()]

    def new_session_id(self, name: str) -> str:
        return f"{name}-{uuid.uuid4().hex[:8]}"


registry = SessionRegistry()
