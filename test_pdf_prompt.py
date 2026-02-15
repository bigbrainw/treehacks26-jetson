#!/usr/bin/env python3
"""Test that PDF context produces correct agent prompt (app + content type, not Python file)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.agent_sdk import build_agent_sdk_prompt


def test_pdf_prompt_includes_context_type():
    """PDF context must include 'PDF document' so model doesn't say 'Python file'."""
    prompt, system = build_agent_sdk_prompt(
        window_title="Neurable_Whitepaper.pdf — Page 5 of 42",
        reading_section="Page 5 of 42",
        app_name="Preview",
        context_type="pdf",
    )
    assert "PDF document" in prompt or "pdf" in prompt.lower(), f"Missing PDF hint: {prompt[:300]}"
    assert "Preview" in prompt, f"Missing app name: {prompt[:300]}"
    assert "Neurable_Whitepaper" in prompt
    assert "Python file" not in prompt
    print("  ✓ PDF prompt includes app: Preview, content type: PDF document")


def test_code_file_prompt():
    """Code/editor context gets correct type."""
    prompt, _ = build_agent_sdk_prompt(
        window_title="main.py - treehacks26",
        app_name="Cursor",
        context_type="file",
    )
    assert "code/editor" in prompt or "file" in prompt.lower()
    assert "Cursor" in prompt
    print("  ✓ Code prompt includes app: Cursor, content type: code/editor")


if __name__ == "__main__":
    print("\n--- PDF prompt test ---")
    test_pdf_prompt_includes_context_type()
    test_code_file_prompt()
    print("\nPASS\n")
