import unittest

from app import crossref


class CrossrefTests(unittest.TestCase):
    def test_normalize_preserves_doi_authors_abstract_and_publication_date(self):
        record = crossref.normalize({
            "DOI": "10.5555/example.1", "title": ["<i>Quantum</i> study"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "abstract": "<jats:p>Evidence and methods.</jats:p>",
            "published-print": {"date-parts": [[2024, 4]]}, "container-title": ["Journal A"],
        })
        self.assertEqual(record["url"], "https://doi.org/10.5555/example.1")
        self.assertEqual(record["authors"], ["Ada Lovelace"])
        self.assertIn("Evidence and methods.", record["content"])
        self.assertEqual(record["published_at"].month, 4)

    def test_missing_doi_is_not_stored_as_a_research_document(self):
        self.assertIsNone(crossref.normalize({"title": ["Unlinked result"]}))

if __name__ == "__main__":
    unittest.main()
