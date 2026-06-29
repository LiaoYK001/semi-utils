import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core import util
from core.util import (
    get_image_rating,
    list_files,
    normalize_rating,
    normalize_rating_percent,
)


class ImageRatingTest(unittest.TestCase):
    def setUp(self):
        util._RATING_CACHE.clear()

    def test_normalize_rating_accepts_only_1_to_5(self):
        self.assertEqual(
            [normalize_rating(value) for value in ["1", 2, "3.0", 4.0, "5"]],
            [1, 2, 3, 4, 5],
        )
        self.assertIsNone(normalize_rating(None))
        self.assertIsNone(normalize_rating(""))
        self.assertIsNone(normalize_rating("0"))
        self.assertIsNone(normalize_rating("6"))
        self.assertIsNone(normalize_rating("bad"))

    def test_normalize_rating_percent_maps_windows_values(self):
        self.assertEqual(normalize_rating_percent("1"), 1)
        self.assertEqual(normalize_rating_percent("25"), 2)
        self.assertEqual(normalize_rating_percent("50"), 3)
        self.assertEqual(normalize_rating_percent("75"), 4)
        self.assertEqual(normalize_rating_percent("99"), 5)
        self.assertEqual(normalize_rating_percent("100"), 5)
        self.assertIsNone(normalize_rating_percent("0"))
        self.assertIsNone(normalize_rating_percent(""))
        self.assertIsNone(normalize_rating_percent("bad"))

    def test_get_image_rating_reads_sidecar_xmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "rated.jpg"
            Image.new("RGB", (8, 8), "white").save(image_path)
            image_path.with_suffix(".xmp").write_text(
                '<x:xmpmeta><rdf:Description xmp:Rating="4"/></x:xmpmeta>',
                encoding="utf-8",
            )

            self.assertEqual(get_image_rating(str(image_path)), 4)

    def test_get_image_rating_reads_embedded_xmp_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "embedded.jpg"
            image_path.write_bytes(
                b"\xff\xd8<x:xmpmeta><rdf:Description xmp:Rating=\"5\"/></x:xmpmeta>\xff\xd9"
            )

            self.assertEqual(get_image_rating(str(image_path)), 5)

    def test_get_image_rating_returns_none_without_rating(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "plain.jpg"
            Image.new("RGB", (8, 8), "white").save(image_path)

            self.assertIsNone(get_image_rating(str(image_path)))

    def test_list_files_adds_rating_to_file_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / "rated.jpg"
            Image.new("RGB", (8, 8), "white").save(image_path)
            image_path.with_suffix(".xmp").write_text(
                '<x:xmpmeta><rdf:Description xmp:Rating="3"/></x:xmpmeta>',
                encoding="utf-8",
            )

            tree = list_files(str(root), {".jpg"})

            self.assertEqual(
                tree,
                [
                    {
                        "label": "rated.jpg",
                        "value": str(image_path.resolve()),
                        "is_file": True,
                        "rating": 3,
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
