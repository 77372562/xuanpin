"""统一路径: 脚本运行时以项目目录为根; 打包成exe后以exe所在目录为根"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):  # PyInstaller打包后
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
CONFIG_PATH = BASE_DIR / "config.yaml"
KEYWORDS_PATH = BASE_DIR / "keywords.txt"
