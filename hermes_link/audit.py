from __future__ import annotations

import hashlib


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def safe_prompt_summary(prompt: str, log_prompt: bool = False) -> str:
    if log_prompt:
        return prompt[:200]
    return "sha256:" + prompt_hash(prompt)
