"""MCPクライアント(Claude Code等)から起動するためのエントリポイント。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.mcp_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()
