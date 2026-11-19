"""run_mlz.py: mlz as the pipeline runs it, from mlz-core's own code only.

Every step runs in the customer's checkout, which a pull request controls. `python3 -m mlz` puts the working folder
first on Python's path, so a folder mlz/ or a json.py at the checkout's top would run in place of mlz-core's mlz or the
standard library, in a step that may hold a token or an Azure sign-in. PYTHONSAFEPATH, which role.yml sets, stops that
on Python 3.11 and later only: older ones ignore it. So the pipeline runs mlz as

    python3 -I .mlz-core/tools/ci/run_mlz.py <command> [<argument> ...]     in a workflow step or a shell script
    subprocess.run(mlz(<command>, <argument>, ...))                           in tools/ci's Python scripts

Isolated (-I), Python adds neither the working folder nor this file's folder to its path, and ignores PYTHONPATH and
every other PYTHON* variable, on every version. This file then puts mlz-core's tools/ first on the path and runs mlz as
`python3 -m mlz` would. mlz needs only the standard library, which -I keeps.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve()


def mlz(*args: str) -> list[str]:
    """The command line that runs `mlz <args>` from mlz-core's tools only."""
    return [sys.executable, "-I", str(HERE), *args]


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parents[1]))  # tools/
    runpy.run_module("mlz", run_name="__main__", alter_sys=True)
