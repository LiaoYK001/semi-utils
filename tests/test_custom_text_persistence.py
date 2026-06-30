import tempfile
import unittest
from pathlib import Path

import app
import core.cache as cache


class CustomTextPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "cache.db")
        cache.set_cache_db_path(self.db_path)
        cache._local.__dict__.clear()
        self.client = app.api.test_client()

    def tearDown(self):
        conn = getattr(cache._local, "conn", None)
        if conn is not None:
            conn.close()
        cache._local.__dict__.clear()
        self.tmp.cleanup()

    def test_custom_text_survives_clear_file_cache(self):
        cache.set_custom_text("image-a.jpg", "Temple")

        response = self.client.delete("/api/v1/cache")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get_custom_text("image-a.jpg"), "Temple")

    def test_custom_texts_api_batch_save_overwrite_and_delete(self):
        response = self.client.post(
            "/api/v1/cache/custom-texts",
            json={"customTexts": {"a.jpg": "A", "b.jpg": "B"}},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/v1/cache/custom-texts")
        self.assertEqual(response.get_json()["custom_texts"], {"a.jpg": "A", "b.jpg": "B"})

        response = self.client.post(
            "/api/v1/cache/custom-texts",
            json={"customTexts": {"a.jpg": "A2", "b.jpg": ""}},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/v1/cache/custom-texts")
        self.assertEqual(response.get_json()["custom_texts"], {"a.jpg": "A2"})

    def test_custom_texts_delete_api_clears_only_location_tags(self):
        cache.set_custom_text("image-a.jpg", "Temple")

        response = self.client.delete("/api/v1/cache/custom-texts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get_all_custom_texts(), {})


if __name__ == "__main__":
    unittest.main()
