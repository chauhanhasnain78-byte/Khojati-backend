import os
import sys

# Add parent directory to sys.path so modules (routers, services, etc.) can be resolved.
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from main import app  # noqa: E402, F401
