"""Exercise browser handoff logic without a live installation or email."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed for browser handoff unit tests")
def test_blog_transfer_browser_logic():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "--test", "tests/js/blog-transfer.test.cjs"], cwd=root, check=True)
