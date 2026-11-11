import unittest
from types import SimpleNamespace

from app.engine import classify, extract, fingerprint, rank_documents, short_summary


class ProcessingTests(unittest.TestCase):
    def test_html_extraction_removes_repeated_navigation_and_normalizes_text(self):
        title, text = extract("<html><title>Study</title><nav>Home About</nav><main><p>Evidence   from a useful study.</p></main></html>", "text/html")
        self.assertEqual(title, "Study")
        self.assertEqual(text, "Study Evidence from a useful study.")
        self.assertNotIn("Home", text)

    def test_fingerprint_normalizes_case_and_punctuation(self):
        self.assertEqual(fingerprint("AI: builds useful tools!"), fingerprint("ai builds useful tools"))

    def test_classification_can_assign_more_than_one_category(self):
        categories = classify("AI research", "A scientific study of artificial intelligence software")
        self.assertIn("Artificial Intelligence", categories)
        self.assertIn("Science", categories)

    def test_retrieval_ranks_term_overlap_and_omits_unmatched_documents(self):
        documents = [
            SimpleNamespace(title="Quantum hardware", summary="Quantum computing research", content="Qubit hardware", categories=["Science"]),
            SimpleNamespace(title="Garden notes", summary="Soil and plants", content="Spring vegetables", categories=["Other"]),
        ]
        ranked = rank_documents("quantum computing", documents, 10)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0].title, "Quantum hardware")
        self.assertGreater(ranked[0][1], 0)

    def test_summary_is_bounded(self):
        summary = short_summary("Evidence. " + "long text " * 100, limit=64)
        self.assertLessEqual(len(summary), 64)


if __name__ == "__main__":
    unittest.main()
