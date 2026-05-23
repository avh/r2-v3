"""R2 Personal Assistant Server — FastAPI + WebSocket."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

SERVER_START_TIME = int(time.time())

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent.parent
WWW = ROOT / "www"


def _user_dir() -> Path:
    val = os.environ.get("R2_BASE_DIR")
    return Path(val) if val else ROOT / "user"

app = FastAPI(title="R2 Personal Assistant")
app.mount("/static", StaticFiles(directory=str(WWW)), name="static")

from src.config import load_config
from src.session import registry, PASession
from src.agents.personal.agent import run_pa_session, handle_ta_direct


def _load_pa_config(pa_name: str) -> dict:
    return load_config(
        ROOT / "config.json",
        ROOT / "src" / "agents" / "personal" / "config.json",
        _user_dir() / pa_name / "config.json",
    )


@app.on_event("startup")
async def startup():
    pas = registry.list_pa_names()
    print(f"[R2] Personal Agents: {pas}")


@app.get("/")
async def index():
    return FileResponse(str(WWW / "index.html"))


@app.get("/api/pas")
async def list_pas():
    return JSONResponse(sorted(registry.list_pa_names()))


@app.post("/api/pas")
async def create_pa_api(request: Request):
    data = await request.json()
    pa_name = (data.get("name") or "").strip()
    if not pa_name:
        return JSONResponse({"error": "name required"}, status_code=400)
    pa_dir = _user_dir() / pa_name
    pa_dir.mkdir(parents=True, exist_ok=True)
    _init_pa_memory(pa_name)
    return JSONResponse({"pa_name": pa_name})


@app.get("/api/tree")
async def get_tree():
    base = _user_dir()
    result = []
    if not base.exists():
        return JSONResponse(result)
    def _ctime(d: Path) -> float:
        st = d.stat()
        return getattr(st, "st_birthtime", st.st_ctime)

    for pa_dir in sorted(base.iterdir()):
        if not pa_dir.is_dir() or pa_dir.name.startswith("."):
            continue
        sessions = []
        for sess_dir in sorted(pa_dir.iterdir(), key=_ctime):
            if not sess_dir.is_dir() or sess_dir.name.startswith("."):
                continue
            sess = registry.get(pa_dir.name, sess_dir.name)
            ta_sessions = []
            if sess:
                ta_sessions = [
                    {"agent_name": n, "ta_session_id": ts.session_id, "status": ts.status}
                    for n, ts in sess.ta_sessions.items()
                ]
            sessions.append({"session_id": sess_dir.name, "ta_sessions": ta_sessions})
        result.append({"pa_name": pa_dir.name, "sessions": sessions})
    return JSONResponse(result)


@app.get("/api/ta/{agent_name}/prompt")
async def get_ta_prompt(agent_name: str):
    prompt_path = ROOT / "src" / "agents" / agent_name / "system.txt"
    if prompt_path.exists():
        return JSONResponse({"prompt": prompt_path.read_text().strip()})
    return JSONResponse({"prompt": ""})


@app.get("/api/{pa_name}/sessions")
async def list_sessions_api(pa_name: str):
    pa_dir = _user_dir() / pa_name
    if not pa_dir.exists():
        return JSONResponse([])
    sessions = sorted(
        [{"session_id": d.name, "title": d.name}
         for d in pa_dir.iterdir()
         if d.is_dir() and not d.name.startswith(".")],
        key=lambda s: s["session_id"],
    )
    return JSONResponse(sessions)


@app.post("/api/{pa_name}/sessions")
async def create_session_api(pa_name: str):
    session_id = registry.new_session_id(pa_name)
    session_dir = _user_dir() / pa_name / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    _init_pa_memory(pa_name)
    return JSONResponse({"session_id": session_id})


def _init_pa_memory(pa_name: str):
    memory_path = _user_dir() / pa_name / "memory.txt"
    if not memory_path.exists():
        memory_path.write_text(f"Your name is {pa_name}.\n")


@app.delete("/api/{pa_name}/sessions/{session_id}")
async def delete_session_api(pa_name: str, session_id: str):
    registry.close(pa_name, session_id)
    sess_dir = _user_dir() / pa_name / session_id
    if sess_dir.exists():
        shutil.rmtree(sess_dir)
    return JSONResponse({"ok": True})


@app.delete("/api/pas/{pa_name}")
async def delete_pa_api(pa_name: str):
    registry.close_pa(pa_name)
    pa_dir = _user_dir() / pa_name
    if pa_dir.exists():
        shutil.rmtree(pa_dir)
    return JSONResponse({"ok": True})


@app.websocket("/ws/{pa_name}/{session_id}")
async def websocket_endpoint(websocket: WebSocket, pa_name: str, session_id: str):
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(msg: dict):
        async with send_lock:
            try:
                await websocket.send_text(json.dumps(msg))
            except Exception:
                pass

    await send({"type": "server_info", "start_time": SERVER_START_TIME})

    config = _load_pa_config(pa_name)
    session, created = registry.get_or_create(pa_name, session_id, config, send)

    # Replay message history to a client reconnecting to an existing session
    if not created:
        for msg in session.msg_log:
            await send(msg)

    # Start the PA processing task if not already running
    if session.task is None or session.task.done():
        session.task = asyncio.create_task(run_pa_session(session))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "message":
                await session.queue.put(msg)

            elif msg_type == "session_list":
                await send({"type": "session_list", "sessions": registry.list_sessions(pa_name)})

            elif msg_type == "create_session":
                new_id = msg.get("session_id") or registry.new_session_id(pa_name)
                new_session, _ = registry.get_or_create(pa_name, new_id, config, send)
                if new_session.task is None or new_session.task.done():
                    new_session.task = asyncio.create_task(run_pa_session(new_session))
                await send({"type": "session_list", "sessions": registry.list_sessions(pa_name)})
                await send({"type": "session_selected", "session_id": new_id})

            elif msg_type == "select_session":
                sel_id = msg.get("session_id")
                sel_session = registry.get(pa_name, sel_id)
                if sel_session:
                    sel_session.send_fn = send
                    if sel_session.task is None or sel_session.task.done():
                        sel_session.task = asyncio.create_task(run_pa_session(sel_session))
                    await send({"type": "session_selected", "session_id": sel_id})

            elif msg_type == "ta_input":
                ta_sid = msg.get("ta_session_id")
                answer = msg.get("text", "")
                for ta_sess in session.ta_sessions.values():
                    if ta_sess.session_id == ta_sid:
                        ta_sess.set_answer(answer)
                        break

            elif msg_type == "ta_direct_question":
                agent_name = msg.get("agent_name", "").strip()
                text = msg.get("text", "").strip()
                if agent_name and text:
                    asyncio.create_task(handle_ta_direct(session, agent_name, text))

            elif msg_type == "close_session":
                close_id = msg.get("session_id")
                registry.close(pa_name, close_id)
                await send({"type": "session_list", "sessions": registry.list_sessions(pa_name)})

    except WebSocketDisconnect:
        pass
