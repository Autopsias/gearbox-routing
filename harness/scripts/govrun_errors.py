"""govrun's refusal exception.

Split into its own module so govrun.py, govrun_pytest_budget.py and
govrun_pytest_ini.py can all raise it without a circular import: govrun.py
imports `check_budget` from govrun_pytest_budget, which imports `ini_addopts`
from govrun_pytest_ini, and the pytest-parsing modules need to raise Refused
too.
"""


class Refused(Exception):
    """Refusal with a message that names the fix. Always exit 2."""
