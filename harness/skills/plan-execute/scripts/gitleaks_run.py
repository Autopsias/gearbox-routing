"""One `gitleaks` process with a single retry on timeout (split out of egress.py)."""
import subprocess

SCANNER_BUSY = "SCANNER-BUSY:"   # a timeout, not a hit


def run_gitleaks(cmd, target, timeout):
    """-> (proc, None), or (None, refusal text): BUSY after two timeouts, fail-closed on OSError."""
    for tries in (1, 2):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout), None
        except OSError as e:
            return None, (f"gitleaks could not scan {target} ({type(e).__name__}) — this tree "
                          "cannot be cleared for egress (fail-closed)")
        except subprocess.TimeoutExpired:
            if tries == 2:
                return None, f"{SCANNER_BUSY} gitleaks timed out twice on {target}: the machine is busy"
