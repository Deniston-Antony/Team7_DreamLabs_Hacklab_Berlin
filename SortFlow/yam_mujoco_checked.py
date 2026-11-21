"""yam_mujoco.py with a success checker added: the same terminal, commands and window.

Start it as you start yam_mujoco.py:  python yam_mujoco_checked.py [--tries 3] [--record]
With --tries N, each task runs as N attempts at once: this window plus N-1 hidden workers (parallel_bench.py), and
the verdict says how many of the N attempts succeeded.
With --record, each attempt's top camera is saved as an MP4 under sortflow_results/videos/, and for N > 1 a grid.mp4
shows them side by side.
It runs Dream Labs' launcher unchanged, but starts viewer_workspace_checked.py in place of viewer_workspace.py.
"""

import os
import subprocess
import sys

import yam_mujoco

_popen = subprocess.Popen


def popen(args, **kwargs):
    """Start the viewer process, swapping in the checked workspace script."""
    return _popen([a.replace("viewer_workspace.py", "viewer_workspace_checked.py") for a in args], **kwargs)


subprocess.Popen = popen


def take_tries(argv):
    """Remove `--tries N` from argv, which Dream Labs' launcher does not know, and return N.
    @param argv Command-line arguments, edited in place.
    @return How many attempts run at once, 1 to 16; 1 when the option is absent.
    """
    if "--tries" not in argv:
        return 1
    at = argv.index("--tries")
    try:
        value = int(argv[at + 1])
        if not 1 <= value <= 16:
            raise ValueError
    except (IndexError, ValueError):
        raise SystemExit("--tries takes a whole number from 1 to 16, such as: --tries 3") from None
    del argv[at : at + 2]
    return value


if __name__ == "__main__":
    from prompt_toolkit.patch_stdout import patch_stdout

    tries = take_tries(sys.argv)
    record = "--record" in sys.argv
    if record:
        sys.argv.remove("--record")
    os.environ["SORTFLOW_TRIES"] = str(tries)  # the viewer process inherits it
    os.environ["SORTFLOW_RECORD"] = "1" if record else "0"
    print("  SortFlow: success checker on. A run stops itself when the item is in the bowl or on the plate.")
    if tries > 1:
        print(f"  Attempts per task: {tries} at once (this window + {tries - 1} hidden workers); each costs credits")
    if record:
        print("  Recording: each task's top camera is saved under sortflow_results/videos/")
    with patch_stdout():
        yam_mujoco.main()
