import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def png():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (12, 34, 56)).save(output, "PNG")
    return output.getvalue()
