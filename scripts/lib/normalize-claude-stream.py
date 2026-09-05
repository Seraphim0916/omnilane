#!/usr/bin/env python3
"""Normalize a successful Claude stream into Omnilane's result contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("events", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    last_result: str | None = None
    last_result_error = False
    last_top_level_text: str | None = None
    with args.events.open(encoding="utf-8") as stream:
        for raw_line in stream:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result" and isinstance(event.get("result"), str):
                last_result = event["result"]
                last_result_error = event.get("is_error") is True
                continue
            if event.get("type") != "assistant" or event.get("parent_tool_use_id"):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            text_blocks = [
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            if text_blocks:
                last_top_level_text = "\n".join(text_blocks)

    if last_result is not None:
        if last_result_error:
            return 1
    elif last_top_level_text is None:
        return 1
    else:
        last_result = last_top_level_text
        canonical = {
            "type": "result",
            "is_error": False,
            "result": last_result,
            "normalized_by": "omnilane",
        }
        with args.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")

    args.output.write_text(last_result.rstrip("\n") + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
