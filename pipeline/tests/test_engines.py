"""Engine loader shapes that vary between cached sets."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from pipeline.core.config import Dataset
from pipeline.engines import mistral

_BLOCKS = [{"type": "text", "bbox": [1, 2, 3, 4], "text": "hi"}]


class MistralLoadTest(TestCase):
    """A page holds either the block list or the whole response object
    ({markdown, blocks}) — both load to the same block list."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mistral-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "engines" / "mistral").mkdir(parents=True)
        self.ds = Dataset(name="t", root=self.tmp)

    def _write(self, page: str, payload: object) -> None:
        (self.tmp / "engines" / "mistral" / f"{page}.json").write_text(
            json.dumps(payload)
        )

    def test_block_list_shape(self) -> None:
        self._write("p1", _BLOCKS)
        self.assertEqual(mistral.load(self.ds, "p1"), _BLOCKS)

    def test_whole_response_shape(self) -> None:
        self._write("p2", {"markdown": "hi", "blocks": _BLOCKS})
        self.assertEqual(mistral.load(self.ds, "p2"), _BLOCKS)

    def test_missing_page_is_none(self) -> None:
        self.assertIsNone(mistral.load(self.ds, "nope"))
