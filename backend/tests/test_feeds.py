import unittest

from app.feeds import parse_feed


class FeedParserTests(unittest.TestCase):
    def test_rss_entries_capture_metadata(self):
        feed = parse_feed("""<rss version='2.0'><channel><title>Lab notes</title><item><title>New result</title><link>https://example.org/a</link><description>Measured result</description><pubDate>Mon, 02 Jan 2023 15:04:05 GMT</pubDate></item></channel></rss>""")
        self.assertEqual(feed["title"], "Lab notes")
        self.assertEqual(feed["entries"][0]["link"], "https://example.org/a")
        self.assertEqual(feed["entries"][0]["summary"], "Measured result")
        self.assertEqual(feed["entries"][0]["published_at"].year, 2023)

    def test_atom_feed_handles_namespaces_and_alternate_link(self):
        feed = parse_feed("""<feed xmlns='http://www.w3.org/2005/Atom'><title>Updates</title><entry><title>Release</title><link rel='alternate' href='https://example.org/release'/><updated>2024-03-01T12:00:00Z</updated><summary>Version notes</summary></entry></feed>""")
        self.assertEqual(feed["entries"][0]["link"], "https://example.org/release")
        self.assertEqual(feed["entries"][0]["published_at"].year, 2024)

    def test_invalid_feed_is_reported(self):
        with self.assertRaises(ValueError):
            parse_feed("<html>not a feed</html>")


if __name__ == "__main__":
    unittest.main()
