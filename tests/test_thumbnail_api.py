import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import api


class ThumbnailApiTest(unittest.TestCase):
    def setUp(self):
        self.client = api.test_client()

    def test_thumbnail_returns_jpeg_for_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "sample.jpg"
            Image.new("RGB", (64, 48), "white").save(image_path)

            response = self.client.get(
                "/api/v1/file/thumb",
                query_string={"path": str(image_path), "size": 128},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/jpeg")
            self.assertGreater(len(response.data), 0)

    def test_thumbnail_rejects_missing_file(self):
        response = self.client.get(
            "/api/v1/file/thumb",
            query_string={"path": "missing.jpg"},
        )

        self.assertEqual(response.status_code, 404)

    def test_thumbnail_rejects_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = self.client.get(
                "/api/v1/file/thumb",
                query_string={"path": tmp},
            )

            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
