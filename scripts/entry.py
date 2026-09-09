"""
The entry point for the .exe.

PyInstaller expects a file rather than a module, so this small file is
used instead of `python -m pult`.
"""
import multiprocessing
import os
import sys

if getattr(sys, "frozen", False):
    # Inside a built .exe the module path is different
    sys.path.insert(0, os.path.dirname(sys.executable))

from pult.__main__ import main  # noqa: E402

if __name__ == "__main__":
    # So the program does not start over when Windows clones the
    # process
    multiprocessing.freeze_support()
    sys.exit(main())
