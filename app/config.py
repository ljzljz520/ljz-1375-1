"""运行期路径配置。所有路径可被环境变量覆盖，便于测试隔离。"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("YUGANG_DATA", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = Path(os.environ.get("YUGANG_DB", DATA_DIR / "harbor.db"))
ASSET_DIR = Path(os.environ.get("YUGANG_ASSETS", DATA_DIR / "assets"))
RELEASE_DIR = Path(os.environ.get("YUGANG_RELEASES", DATA_DIR / "releases"))
CURRENT_LINK = RELEASE_DIR / "current"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

DEFAULT_EDITOR = int(os.environ.get("YUGANG_EDITOR", "1"))
