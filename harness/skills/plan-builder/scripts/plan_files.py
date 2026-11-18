#!/usr/bin/env python3
"""Write plan files the way the repo pre-commit hooks require.

Split out of build_plan.py to keep it under its size baseline.
"""
from pathlib import Path


def clean_text(text):
    """`text` with no trailing whitespace on any line and one final newline.

    Repo pre-commit hooks (trailing-whitespace, end-of-file-fixer) rewrite any plan
    file that breaks those rules and block the commit, so every plan commit failed once.
    The PLAN.html render applies it too, so a re-render (plan_mutate's drift check)
    matches the page on disk byte for byte.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_clean_text(path, text):
    Path(path).write_text(clean_text(text))
