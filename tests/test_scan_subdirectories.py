import tempfile
import unittest
from pathlib import Path

from PIL import Image

import app


class ScanSubdirectoriesApiTest(unittest.TestCase):
    def test_file_tree_respects_scan_subdirectories_config(self):
        old_values = {
            key: app.config.get("DEFAULT", key, fallback=None)
            for key in ("input_folder", "output_folder", "scan_subdirectories")
        }

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                input_root = root / "input"
                output_root = root / "output"
                nested = input_root / "nested"
                input_root.mkdir()
                output_root.mkdir()
                nested.mkdir()
                Image.new("RGB", (8, 8), "white").save(input_root / "top.jpg")
                Image.new("RGB", (8, 8), "white").save(nested / "deep.jpg")

                app.config.set("DEFAULT", "input_folder", str(input_root))
                app.config.set("DEFAULT", "output_folder", str(output_root))
                app.config.set("DEFAULT", "scan_subdirectories", "False")

                client = app.api.test_client()
                data = client.get("/api/v1/file/tree").get_json()
                input_children = data["input_files"][0]["children"]

                self.assertEqual([node["label"] for node in input_children], ["top.jpg"])

                app.config.set("DEFAULT", "scan_subdirectories", "True")
                data = client.get("/api/v1/file/tree").get_json()
                input_children = data["input_files"][0]["children"]

                self.assertIn("nested", [node["label"] for node in input_children])
        finally:
            for key, value in old_values.items():
                if value is not None:
                    app.config.set("DEFAULT", key, value)


if __name__ == "__main__":
    unittest.main()
