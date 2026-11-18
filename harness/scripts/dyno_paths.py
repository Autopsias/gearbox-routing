"""Path constants shared by the cadence hook and the self-assessment appender."""
import os

# Touched by append_reflection.py after each successful append; read stat-only by the hook.
REVIEW_STAMP = os.path.expanduser("~/.gearbox-state/last-whole-history-review")
