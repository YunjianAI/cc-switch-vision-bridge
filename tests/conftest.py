from __future__ import annotations

import io

import pytest
from PIL import Image


@pytest.fixture
def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (255, 200, 0)).save(buffer, format="PNG")
    return buffer.getvalue()

