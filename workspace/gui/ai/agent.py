"""The on-device assistant: chat sessions and the Claude tool loop.

Modeled on the AI builder's agent (dorna_ai_builder/builder/agent.py): a
manual tool loop over the streaming Messages API, NDJSON events to the
chat panel (text / think / tool / tool_pending / tool_done / ui / error /
done), sessions saved under ~/.workspace/ai/sessions/, cancel, and the
history repaired after a turn that was cut short.

The model is reached through the Dorna account server's relay
(<DORNA_ACCOUNT_SERVER>/s/device/api/ai), authenticated with this unit's
device token: the server holds the key and meters the account's usage. No
API key is ever on the robot.
"""
import asyncio
import json
import os
import time
import traceback
import uuid

import anthropic

from gui.account import account
from gui.account.client import server_url
from gui.data_home import data_path

from . import uploads
from .prompts import system_blocks
from .tools import TOOLS, ToolError, run_tool, tool_label

MODEL = os.environ.get("WORKSPACE_AI_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("WORKSPACE_AI_EFFORT", "medium")
MAX_ROUNDS = 60
MAX_RESULT_CHARS = 30_000
UPDATES_MODELS = ("claude-opus-5-5", "claude-fable-5-1")   # between-tool notes come as "updates" thinking

_CLIENT = None          # (token, server, client)


class NotLoggedIn(Exception):
    pass


def client():
    """The SDK client for the current login (rebuilt when the token changes)."""
    global _CLIENT
    token, server = account.token(), server_url()
    if not token:
        raise NotLoggedIn()
    if _CLIENT is None or _CLIENT[0] != token or _CLIENT[1] != server:
        c = anthropic.AsyncAnthropic(
            api_key="device", auth_token=token, base_url=server + "/s/device/api/ai",
            max_retries=2, timeout=anthropic.Timeout(600.0, connect=20.0))
        c.api_key = None            # only the device bearer token goes out (no x-api-key header)
        _CLIENT = (token, server, c)
    return _CLIENT[2]


def _updates_display():
    return any(MODEL.startswith(m) for m in UPDATES_MODELS)


OUT_OF_CREDITS = "Out of AI credits. Contact the Dorna team to add more."


def _sessions_dir():
    d = data_path("ai", "sessions", "x")
    return os.path.dirname(d)


# ── sessions ─────────────────────────────────────────────────────────

class Session:
    def __init__(self, sid=None, owner=None):
        self.id = sid or uuid.uuid4().hex[:12]
        self.owner = owner           # the account email that had this conversation
        self.project = None          # the project the user had open last
        self.messages = []
        self.transcript = []
        self.cancel = False
        self.lock = asyncio.Lock()
        self.live = []
        self._wake = None

    @property
    def path(self):
        return os.path.join(_sessions_dir(), f"{self.id}.json")

    def save(self):
        # image / PDF data stays in the upload store, not in the session file
        data = {"id": self.id, "owner": self.owner, "project": self.project,
                "messages": uploads.strip_binary(self.messages),
                "transcript": self.transcript, "saved": time.time()}
        tmp = self.path + ".tmp"
        # transcripts hold project content and the account email: owner-only
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, default=str)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, sid):
        s = cls(sid)
        with open(s.path) as f:
            data = json.load(f)
        s.owner = data.get("owner")
        s.project = data.get("project")
        s.messages = data.get("messages") or []
        s.transcript = data.get("transcript") or []
        return s

    def public(self):
        return {"id": self.id, "project": self.project, "transcript": self.transcript,
                "busy": self.lock.locked(), "live_seq": len(self.live)}

    def publish(self, ev):
        self.live.append(ev)
        if self._wake:
            self._wake.set()

    async def wait_live(self, timeout):
        if self._wake is None or self._wake.is_set():
            self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except asyncio.TimeoutError:
            pass


_SESSIONS = {}


def _valid_sid(sid):
    return bool(sid) and len(sid) <= 32 and all(c in "0123456789abcdef" for c in sid)


def get_session(sid=None, owner=None, create=True):
    """That conversation, when it belongs to ``owner``; else a new one (one
    account never sees another's chat on a shared robot)."""
    s = _SESSIONS.get(sid) if _valid_sid(sid) else None
    if s is None and _valid_sid(sid) and os.path.isfile(os.path.join(_sessions_dir(), f"{sid}.json")):
        try:
            s = Session.load(sid)
        except (OSError, ValueError):
            s = None
    if s is not None and owner is not None and s.owner not in (None, owner):
        s = None
    if s is None:
        if not create:
            return None
        s = Session(owner=owner)
        s.save()
    _SESSIONS[s.id] = s
    return s


def recent_sessions(owner, limit=10):
    """The account's last ``limit`` conversations on this robot, newest
    first — the ones with at least one message: {id, title, project, saved}."""
    d = _sessions_dir()
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json") and _valid_sid(n[:-5])]
    except OSError:
        return []
    names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    out = []
    for n in names:
        try:
            with open(os.path.join(d, n)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if data.get("owner") not in (None, owner):
            continue
        first = next((t.get("text") for t in data.get("transcript") or []
                      if t.get("role") == "user" and t.get("text")), None)
        if not first:
            continue
        out.append({"id": data.get("id") or n[:-5], "title": " ".join(str(first).split())[:80],
                    "project": data.get("project"), "saved": data.get("saved")})
        if len(out) >= limit:
            break
    return out


# ── the loop ─────────────────────────────────────────────────────────

def _request_kwargs(session):
    kw = dict(
        model=MODEL,
        max_tokens=32000,
        system=system_blocks(),
        tools=TOOLS,
        messages=session.messages,
        thinking={"type": "adaptive", "display": "updates" if _updates_display() else "summarized"},
        output_config={"effort": EFFORT},
        cache_control={"type": "ephemeral"},
        betas=["context-management-2025-06-27"],
        context_management={"edits": [{"type": "clear_tool_uses_20250919"}]},
    )
    if _updates_display():
        kw["betas"].append("thinking-display-updates-2026-08-18")
    return kw


def _truncate(text):
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    if len(text) > MAX_RESULT_CHARS:
        return text[:MAX_RESULT_CHARS] + f"\n… [cut: {len(text) - MAX_RESULT_CHARS} more chars — narrow the request]"
    return text


def user_content(session, user_text, context=None, attachments=None):
    """The user message's content: the attached files first (image and
    document blocks, or a note naming the saved file), then the text."""
    text = user_text
    if context:
        text += f"\n\n<page_context>{json.dumps(context, default=str)[:4000]}</page_context>"
    if not attachments:
        return text
    return uploads.content_blocks(session.id, attachments) + [{"type": "text", "text": text}]


async def run_turn(session, user_text, emit, context=None, attachments=None):
    """One user turn to completion, streaming events through ``emit``."""
    if isinstance(context, dict) and context.get("project"):
        session.project = str(context["project"])
    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, user_content, session, user_text, context, attachments)
    repair_history(session.messages)
    session.messages.append({"role": "user", "content": content})
    repair_history(session.messages)
    files = [uploads.public(r) for r in (uploads.get(session.id, i) for i in attachments or []) if r]
    session.transcript.append({"role": "user", "text": user_text, **({"files": files} if files else {})})
    reply = {"role": "assistant", "blocks": []}
    session.transcript.append(reply)
    session.cancel = False

    def add_text(delta):
        if reply["blocks"] and reply["blocks"][-1]["kind"] == "text":
            reply["blocks"][-1]["text"] += delta
        else:
            reply["blocks"].append({"kind": "text", "text": delta})

    notes = _updates_display()
    json_retries = 0
    try:
        for _round in range(MAX_ROUNDS):
            if session.cancel:
                break
            try:
                async with client().beta.messages.stream(**_request_kwargs(session)) as stream:
                    async for event in stream:
                        if session.cancel:
                            break
                        if event.type == "text":
                            add_text(event.text)
                            await emit({"t": "text", "d": event.text})
                        elif event.type == "thinking":
                            if notes:
                                add_text(event.thinking)
                                await emit({"t": "text", "d": event.thinking})
                            else:
                                await emit({"t": "think", "d": event.thinking})
                        elif notes and event.type == "content_block_stop" \
                                and getattr(event, "content_block", None) is not None \
                                and event.content_block.type == "thinking" and event.content_block.thinking:
                            add_text("\n\n")
                            await emit({"t": "text", "d": "\n\n"})
                        elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                            await emit({"t": "tool_pending", "id": event.content_block.id,
                                        "name": event.content_block.name})
                    if session.cancel:
                        break
                    response = await stream.get_final_message()
                json_retries = 0
            except ValueError:
                json_retries += 1         # a tool input that didn't parse as JSON: ask again
                if json_retries > 2:
                    raise
                continue

            session.messages.append({"role": "assistant", "content": response.to_dict()["content"]})
            if response.stop_reason == "refusal":
                msg = "I can't help with that request."
                add_text(msg)
                await emit({"t": "text", "d": msg})
                break
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            results = []
            for block in tool_uses:
                args = block.input
                label = tool_label(block.name, args)
                item = {"kind": "tool", "id": block.id, "name": block.name, "label": label, "ok": None}
                reply["blocks"].append(item)
                await emit({"t": "tool", "id": block.id, "name": block.name, "label": label})
                events = []
                if session.cancel:
                    text, ok = "cancelled by the user", False
                elif response.stop_reason == "max_tokens":
                    text, ok = "input was cut off (max_tokens) — send it again in smaller pieces", False
                else:
                    try:
                        text, events = await loop.run_in_executor(None, run_tool, session, block.name, args)
                        ok = True
                    except ToolError as ex:
                        text, ok = str(ex), False
                    except Exception as ex:
                        traceback.print_exc()
                        text, ok = f"internal error: {type(ex).__name__}: {ex}", False
                item["ok"] = ok
                if not ok:
                    item["error"] = text[:300]
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": _truncate(text),
                                **({} if ok else {"is_error": True})})
                await emit({"t": "tool_done", "id": block.id, "ok": ok, "error": None if ok else text[:300]})
                for e in events:
                    await emit({"t": "ui", **e})
            session.messages.append({"role": "user", "content": results})
            session.save()
        else:
            msg = "\n\n(I stopped after many steps — tell me to continue.)"
            add_text(msg)
            await emit({"t": "text", "d": msg})
        if session.cancel:
            _close_dangling_tool_uses(session)
            add_text("\n\n(stopped)")
            await emit({"t": "text", "d": "\n\n(stopped)"})
    except (anthropic.AuthenticationError, NotLoggedIn):
        account.invalidate()
        await _fail(session, emit, reply, "This robot is no longer logged in. Log in again (top right) to use the assistant.")
    except anthropic.PermissionDeniedError:
        await _fail(session, emit, reply, OUT_OF_CREDITS)
    except anthropic.RateLimitError:
        await _fail(session, emit, reply, "The assistant is busy right now — wait a moment and try again.")
    except anthropic.APIStatusError as ex:
        await _fail(session, emit, reply, f"The assistant couldn't answer (error {ex.status_code}). Try again in a moment.")
    except anthropic.APIConnectionError:
        await _fail(session, emit, reply, "Couldn't reach the Dorna server — check this robot's internet connection.")
    except Exception as ex:
        traceback.print_exc()
        await _fail(session, emit, reply, f"Something went wrong: {type(ex).__name__}: {ex}")
    finally:
        session.save()
    await emit({"t": "done", "project": session.project})


def _close_dangling_tool_uses(session):
    if not session.messages or session.messages[-1]["role"] != "assistant":
        return
    content = session.messages[-1]["content"]
    ids = [b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] if isinstance(content, list) else []
    if ids:
        session.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": "cancelled by the user", "is_error": True} for i in ids]})


def _blocks(content):
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])


def repair_history(messages):
    """Make a stored history valid again after a cut-short turn: every
    tool_use answered right after it, inputs are objects, and back-to-back
    messages of one role merged (tool results first)."""
    changed = False
    out = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            if m["role"] == "user":
                merged = _blocks(out[-1]["content"]) + _blocks(m["content"])
                merged.sort(key=lambda b: 0 if isinstance(b, dict) and b.get("type") == "tool_result" else 1)
                out[-1] = {"role": "user", "content": merged}
            else:
                out[-1] = {"role": "assistant", "content": _blocks(out[-1]["content"]) + _blocks(m["content"])}
            changed = True
            continue
        out.append(dict(m))
    for i, m in enumerate(out):
        if m["role"] != "assistant" or isinstance(m["content"], str):
            continue
        ids = []
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                ids.append(b["id"])
                if not isinstance(b.get("input"), dict):
                    b["input"] = {}
                    changed = True
        if not ids:
            continue
        nxt = out[i + 1] if i + 1 < len(out) else None
        have = {b.get("tool_use_id") for b in _blocks(nxt["content"])} if nxt and nxt["role"] == "user" else set()
        missing = [{"type": "tool_result", "tool_use_id": t, "is_error": True,
                    "content": "not run — the turn was interrupted"} for t in ids if t not in have]
        if not missing:
            continue
        changed = True
        if nxt and nxt["role"] == "user":
            nxt["content"] = missing + _blocks(nxt["content"])
        else:
            out.insert(i + 1, {"role": "user", "content": missing})
    messages[:] = out
    return changed


async def _fail(session, emit, reply, message):
    _close_dangling_tool_uses(session)
    reply["blocks"].append({"kind": "error", "text": message})
    await emit({"t": "error", "message": message})
