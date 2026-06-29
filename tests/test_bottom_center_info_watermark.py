import unittest

from PIL import Image, ImageChops

from core.jinja2renders import (
    format_aperture,
    format_focal_length,
    format_iso,
    format_photo_time,
    format_shutter_speed,
)
from processor.core import PipelineContext
from processor.filters import BottomCenterInfoWatermarkFilter


class BottomCenterInfoWatermarkTest(unittest.TestCase):
    def test_exif_formatters_normalize_common_values(self):
        exif = {
            "DateTimeOriginal": "2026:06:17 15:26:49",
            "FocalLengthIn35mmFormat": "200.0",
            "ShutterSpeed": "1/640",
            "ApertureValue": "5.6",
            "ISO": "100.0",
        }

        self.assertEqual(format_photo_time(exif), "2026.06.17 15:26:49")
        self.assertEqual(format_focal_length(exif), "200mm")
        self.assertEqual(format_shutter_speed(exif), "1/640s")
        self.assertEqual(format_aperture(exif), "f/5.6")
        self.assertEqual(format_iso(exif), "ISO 100")

    def test_exif_formatters_fallback_to_dash(self):
        self.assertEqual(format_photo_time({}), "-")
        self.assertEqual(format_focal_length({}), "-")
        self.assertEqual(format_shutter_speed({}), "-")
        self.assertEqual(format_aperture({}), "-")
        self.assertEqual(format_iso({}), "-")

    def test_bottom_center_info_watermark_changes_image_without_location(self):
        source = Image.new("RGBA", (640, 360), (20, 80, 120, 255))
        ctx = PipelineContext({
            "buffer": [source],
            "buffer_loaded": True,
            "location_text": "",
            "time_text": "-",
            "focal_text": "-",
            "shutter_text": "-",
            "aperture_text": "-",
            "iso_text": "-",
            "font_path": "AlibabaPuHuiTi-2-45-Light.otf",
            "parameter_font_path": "Roboto-Medium.ttf",
            "color": "white",
            "time_color": "white",
            "parameter_color": "white",
            "location_height": 24,
            "time_height": 18,
            "parameter_height": 20,
            "line_spacing": 6,
            "bottom_offset": 24,
            "parameter_gap": 24,
        })

        BottomCenterInfoWatermarkFilter().process(ctx)

        result = ctx.get_buffer()[0]
        self.assertEqual(result.size, source.size)
        self.assertIsNotNone(ImageChops.difference(source, result).getbbox())


if __name__ == "__main__":
    unittest.main()
