import unittest

from video_downloader import format_download_error


class TestFormatDownloadError(unittest.TestCase):
    def test_instagram_empty_media_response(self):
        exc = Exception(
            "ERROR: [Instagram] DabyOp9CN8n: Instagram sent an empty media response."
        )
        msg = format_download_error(
            exc, "https://www.instagram.com/reel/DabyOp9CN8n/"
        )
        self.assertIn("Instagram blocked the download", msg)
        self.assertIn("cookies.txt", msg)

    def test_instagram_missing_impersonation(self):
        exc = Exception(
            "The extractor is attempting impersonation, but no impersonate target is available."
        )
        msg = format_download_error(
            exc, "https://www.instagram.com/reel/abc123/"
        )
        self.assertIn("curl-cffi", msg)

    def test_youtube_bot_check(self):
        exc = Exception("Sign in to confirm you're not a bot")
        msg = format_download_error(
            exc, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.assertIn("YouTube blocked the download", msg)

    def test_passthrough_for_unknown_errors(self):
        exc = Exception("Something else went wrong")
        msg = format_download_error(exc, "https://example.com/video")
        self.assertEqual("Something else went wrong", msg)


if __name__ == "__main__":
    unittest.main()
