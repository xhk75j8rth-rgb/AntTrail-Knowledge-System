#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.wechat_entry_adapter import main  # noqa: E402
from chat_gateway.link_extractor import extract_urls  # noqa: E402,F401


if __name__ == "__main__":
    sys.exit(main())
