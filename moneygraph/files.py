"""Replace a local result without truncating the previous file on write failure."""
import os
from pathlib import Path
import tempfile


def atomic_text(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
