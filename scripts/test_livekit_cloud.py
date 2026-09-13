"""LiveKit Cloud smoke test. Never prints credentials or participant tokens."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from livekit import rtc

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def attempt(number: int) -> bool:
    headers = {}
    if os.getenv("AI_API_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['AI_API_TOKEN']}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(os.getenv("JARVIS_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/") + "/v1/livekit/token", headers=headers, json={"display_name": "Jarvis smoke test"})
        response.raise_for_status()
        credentials = response.json()
    room = rtc.Room()
    audio_seen = asyncio.Event()
    participant_seen = asyncio.Event()

    @room.on("participant_connected")
    def participant_connected(_participant):
        participant_seen.set()

    @room.on("track_subscribed")
    def track_subscribed(track, _publication, _participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            audio_seen.set()

    try:
        await room.connect(credentials["server_url"], credentials["participant_token"])
        if room.remote_participants:
            participant_seen.set()
        await asyncio.wait_for(participant_seen.wait(), timeout=30)
        try:
            await asyncio.wait_for(audio_seen.wait(), timeout=30)
        except TimeoutError:
            pass
        print(f"attempt={number} connected=true agent=true audio={str(audio_seen.is_set()).lower()}")
        return audio_seen.is_set()
    finally:
        await room.disconnect()


async def main(repeats: int):
    results = [await attempt(index) for index in range(1, repeats + 1)]
    if not all(results):
        raise SystemExit("one or more sessions connected without receiving Jarvis audio")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    asyncio.run(main(max(1, min(parser.parse_args().repeats, 5))))
