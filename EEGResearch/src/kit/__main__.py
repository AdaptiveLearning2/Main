"""AdaptiveLearningSensors.exe, or `python -m src.kit` with KIT_APP_DIR set: see src/kit/launcher.py."""

import multiprocessing
import sys

from src.kit.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
