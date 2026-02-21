"""
config/prompts.py
Prompt file loader — reads .md files from PROMPT_DIR.

This file contains ZERO actual agent prompts. All prompts live in the private
repo (Project-01-Private/agents/prompts/) and are loaded at runtime via the
PROMPT_DIR environment variable.

Local development: set PROMPT_DIR=/path/to/Project-01-Private/agents/prompts
Railway deployment: inject each prompt as an individual environment variable
"""

import os
from pathlib import Path

from config import settings


def load_prompt(prompt_key: str) -> str:
    """
    Load a prompt file by key from PROMPT_DIR.

    Args:
        prompt_key: File name without extension (e.g. 'manager_system').
                    Corresponds to <PROMPT_DIR>/<prompt_key>.md

    Returns:
        Prompt content as a plain string (stripped of leading/trailing whitespace).

    Raises:
        EnvironmentError: PROMPT_DIR is not configured.
        FileNotFoundError: The .md file does not exist in PROMPT_DIR.
    """
    if not settings.PROMPT_DIR:
        raise EnvironmentError(
            "PROMPT_DIR is not set. "
            "Point it to the directory containing your agent prompt .md files. "
            "See .env.example for the required variable."
        )

    prompt_dir = Path(settings.PROMPT_DIR)
    if not prompt_dir.is_dir():
        raise FileNotFoundError(
            f"PROMPT_DIR does not exist or is not a directory: {prompt_dir}"
        )

    prompt_file = prompt_dir / f"{prompt_key}.md"
    if not prompt_file.is_file():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}. "
            f"Ensure '{prompt_key}.md' exists in PROMPT_DIR."
        )

    return prompt_file.read_text(encoding="utf-8").strip()


def load_prompt_from_env(env_key: str, fallback_prompt_key: str | None = None) -> str:
    """
    Load a prompt from an environment variable (Railway pattern).

    On Railway, each prompt is injected as an env var (e.g. PROMPT_MANAGER_SYSTEM).
    Falls back to load_prompt() if the env var is not set and a key is provided.

    Args:
        env_key:            Environment variable name (e.g. 'PROMPT_MANAGER_SYSTEM').
        fallback_prompt_key: Optional prompt_key to try via load_prompt() if env var is absent.

    Returns:
        Prompt content string.
    """
    value = os.getenv(env_key)
    if value:
        return value.strip()

    if fallback_prompt_key:
        return load_prompt(fallback_prompt_key)

    raise EnvironmentError(
        f"Prompt not found. Set the '{env_key}' environment variable "
        f"or configure PROMPT_DIR with a '{fallback_prompt_key}.md' file."
    )


def list_available_prompts() -> list[str]:
    """
    List all prompt keys available in PROMPT_DIR.

    Returns:
        Sorted list of prompt keys (file stems without .md extension).
        Empty list if PROMPT_DIR is not set or does not exist.
    """
    if not settings.PROMPT_DIR:
        return []

    prompt_dir = Path(settings.PROMPT_DIR)
    if not prompt_dir.is_dir():
        return []

    return sorted(p.stem for p in prompt_dir.glob("*.md"))
