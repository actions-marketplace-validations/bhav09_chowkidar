"""Editor integration module to securely open project directories or files in default editors."""

from __future__ import annotations

import logging
import os
import shlex
import platform
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def open_in_editor(file_path_str: str) -> bool:
    """Open a file or its parent directory in the user's default/configured editor.

    Supports:
    - CHOWKIDAR_EDITOR, VISUAL, EDITOR environment variables
    - Auto-detection of 'cursor' or 'code' (VS Code) on PATH
    - Fallback to native OS folder opening (open/xdg-open/explorer)
    """
    path = Path(file_path_str).resolve()
    # Check if the path exists, if not reject to prevent opening arbitrary directories
    if not path.exists():
        logger.warning("Rejecting attempt to open non-existent path: %s", file_path_str)
        return False

    target_path = str(path)
    parent_dir = str(path.parent) if path.is_file() else str(path)

    # 1. Check environment variables
    editor = os.environ.get("CHOWKIDAR_EDITOR") or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        # Handle cases where editor contains spaces or arguments (e.g., 'code --wait') safely with shlex
        parts = shlex.split(editor)
        cmd = parts + [target_path]
        try:
            logger.info("Opening editor with env var command: %s", cmd)
            # Run without shell=True to prevent arbitrary command execution/breakouts
            subprocess.run(cmd, check=True, timeout=10)
            return True
        except Exception as e:
            logger.warning("Failed to open via environment editor '%s': %s", editor, e)

    # 2. Check for Cursor or VS Code on PATH
    for binary in ["cursor", "code"]:
        if shutil.which(binary):
            try:
                logger.info("Opening editor using detected binary '%s' for path: %s", binary, target_path)
                subprocess.run([binary, target_path], check=True, timeout=10)
                return True
            except Exception as e:
                logger.warning("Failed to open via '%s': %s", binary, e)

    # 3. Fallback to OS-native open/explore
    system = platform.system()
    try:
        if system == "Darwin":
            # On macOS, try to open the file with Cursor, VS Code, or default text editor, then fall back to opening parent folder
            for cmd in [
                ["open", "-a", "Cursor", target_path],
                ["open", "-a", "Visual Studio Code", target_path],
                ["open", "-t", target_path],
                ["open", target_path],
                ["open", parent_dir]
            ]:
                try:
                    logger.info("Mac OS open attempt: %s", cmd)
                    subprocess.run(cmd, check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except subprocess.SubprocessError:
                    continue
            return False
        elif system == "Linux":
            logger.info("Fallback Linux: opening %s", parent_dir)
            subprocess.run(["xdg-open", parent_dir], check=True, timeout=10)
            return True
        elif system == "Windows":
            logger.info("Fallback Windows: opening %s", parent_dir)
            if hasattr(os, "startfile"):
                os.startfile(parent_dir)
            else:
                subprocess.run(["explorer", parent_dir], check=True, timeout=10)
            return True
    except Exception as e:
        logger.error("All editor open attempts failed: %s", e)
        return False

    return False
