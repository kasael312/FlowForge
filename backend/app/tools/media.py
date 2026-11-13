"""Media tools: image generation and audio transcription (OpenAI APIs)."""

from __future__ import annotations

import base64
import posixpath
from typing import Any

import httpx

from app.executor.sandbox import REPO_ROOT
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema
from app.tools.code import safe_repo_path

OPENAI = "https://api.openai.com/v1"


class GenerateImage(Tool):
    name = "generate_image"
    description = "Generate an image from a prompt (OpenAI Images API, credential 'openai') and save it as PNG in the repository (under output/ by default)."
    category = ToolCategory.media
    required_credentials = ("openai",)
    needs_sandbox = True
    input_schema = schema(
        {
            "prompt": {"type": "string"},
            "path": {
                "type": "string",
                "description": "Relative .png path, default output/image.png",
            },
            "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"]},
            "model": {"type": "string", "description": "Default gpt-image-1"},
        },
        ["prompt"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        key = ctx.need("openai")
        path = safe_repo_path(args.get("path") or "output/image.png")
        try:
            r = await ctx.client().post(
                f"{OPENAI}/images/generations",
                headers={"authorization": f"Bearer {key}"},
                json={
                    "model": args.get("model") or "gpt-image-1",
                    "prompt": args["prompt"],
                    "size": args.get("size") or "1024x1024",
                    "n": 1,
                },
                timeout=180.0,
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"image request failed: {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            raise ToolError(f"image API HTTP {r.status_code}: {r.text[:300]}")
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            img = await ctx.client().get(item["url"])
            data = img.content
        else:
            raise ToolError("image API returned no image")
        await ctx.sandbox_required().write_bytes(path, data)
        return f"saved {len(data)} bytes to {posixpath.relpath(path, REPO_ROOT)}"


class TranscribeAudio(Tool):
    name = "transcribe_audio"
    description = "Transcribe an audio file from the repository to text (OpenAI transcription API, credential 'openai')."
    category = ToolCategory.media
    required_credentials = ("openai",)
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {"type": "string", "description": "Relative path to mp3/wav/m4a/webm"},
            "language": {"type": "string", "description": "ISO code, optional"},
            "model": {"type": "string", "description": "Default gpt-4o-mini-transcribe"},
        },
        ["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        key = ctx.need("openai")
        path = safe_repo_path(args["path"])
        data = await ctx.sandbox_required().read_bytes(path)
        if len(data) > 25_000_000:
            raise ToolError("audio file exceeds 25 MB")
        form: dict[str, Any] = {"model": args.get("model") or "gpt-4o-mini-transcribe"}
        if args.get("language"):
            form["language"] = args["language"]
        try:
            r = await ctx.client().post(
                f"{OPENAI}/audio/transcriptions",
                headers={"authorization": f"Bearer {key}"},
                data=form,
                files={"file": (posixpath.basename(path), data)},
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"transcription request failed: {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            raise ToolError(f"transcription API HTTP {r.status_code}: {r.text[:300]}")
        return str(r.json().get("text") or "")


MEDIA_TOOLS: list[Tool] = [GenerateImage(), TranscribeAudio()]
