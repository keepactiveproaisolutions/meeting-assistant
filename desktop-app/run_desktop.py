import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
