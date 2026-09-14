"""Jarvis voice-to-voice worker: LiveKit WebRTC + Gemini Live + gated Jarvis tools."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.genai import types
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, RunContext, function_tool, room_io
from livekit.plugins import google

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)
if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

# Voice may approve visible, reversible PC interaction. External communication,
# deletion, arbitrary shell commands, and account/device changes still stop.
VOICE_AUTO_APPROVE_TOOLS = frozenset({
    "computer_open", "computer_browser", "computer_click", "computer_type",
    "computer_key", "blender_create",
})


class JarvisVoiceAgent(Agent):
    def __init__(self) -> None:
        now = datetime.datetime.now().astimezone()
        clock = (f"\nToday is {now.strftime('%A, %B %d, %Y')} and the local time is "
                 f"{now.strftime('%I:%M %p %Z')}, from the host clock. Treat this as "
                 "authoritative; never quote a training-data date, a knowledge cutoff, or "
                 "alternate timelines as the current date, and never pretend you know it differently.")
        super().__init__(instructions=("You are Jarvis, Jerry's concise, warm local system assistant. Speak naturally. "
            "Default to one to three short spoken sentences unless Jerry asks for detail. Begin answering directly without filler. "
            "Never claim you performed a computer, email, calendar, file, web, or Blender action yourself. "
            "For any requested real action, call run_jarvis_command and report its actual result. "
            "The voice bridge may authorize actions for the authenticated owner. Never claim success unless the tool result confirms it. Do not use camera or video." + clock))

    @function_tool
    async def run_jarvis_command(self, context: RunContext, command: str) -> str:
        """Run a real command through Jarvis's existing tool router and safety gates.

        Args:
            command: The user's complete requested system action.
        """
        load_dotenv(ROOT / ".env", override=True)
        headers = {"Content-Type": "application/json"}
        token = os.getenv("AI_API_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"messages": [{"role": "user", "content": command}], "assistant_profile": "general", "max_tool_rounds": 5}
        url = os.getenv("JARVIS_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/") + "/v1/agent/chat"
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                return f"Jarvis action failed with status {response.status_code}: {response.text[:300]}"
            result = response.json()
            auto_approve = os.getenv("JARVIS_LIVEKIT_AUTO_APPROVE", "false").lower() in {"1", "true", "yes", "on"}
            confirmations = 0
            while result.get("confirmation") and auto_approve and confirmations < 8:
                pending = result["confirmation"]
                action = str(pending.get("action") or pending.get("tool") or "")
                if action not in VOICE_AUTO_APPROVE_TOOLS:
                    break
                confirmation_id = str(pending.get("confirmation_id") or "")
                if not confirmation_id:
                    break
                confirm = await client.post(
                    url.rsplit("/", 1)[0] + "/confirm",
                    headers=headers,
                    json={"confirmation_id": confirmation_id, "confirmed": True},
                )
                if confirm.status_code != 200:
                    return f"Jarvis action confirmation failed with status {confirm.status_code}: {confirm.text[:300]}"
                result = confirm.json()
                confirmations += 1
        if result.get("confirmation"):
            confirmation = result["confirmation"]
            return "Confirmation is still required in the Jarvis dashboard: " + str(confirmation.get("reason") or confirmation.get("action") or "sensitive action")
        message = result.get("message") or {}
        return str(message.get("content") or result.get("status") or json.dumps(result))[:5000]


server = AgentServer()


@server.rtc_session(agent_name=os.getenv("JARVIS_LIVEKIT_AGENT_NAME", "jarvis-voice"))
async def jarvis_voice(ctx: agents.JobContext):
    session = AgentSession(llm=google.realtime.RealtimeModel(
        model=os.getenv("JARVIS_LIVEKIT_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"),
        voice=os.getenv("GEMINI_VOICE_NAME", "Orus"),
        temperature=0.55,
        max_output_tokens=384,
        thinking_config=types.ThinkingConfig(include_thoughts=False, thinking_budget=0),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=100,
                silence_duration_ms=350,
            )
        ),
    ))
    await session.start(room=ctx.room, agent=JarvisVoiceAgent(), room_options=room_io.RoomOptions(video_input=False))
    await session.generate_reply(instructions="Greet Jerry briefly and say Live Conversation is ready.")


if __name__ == "__main__":
    agents.cli.run_app(server)
