import unittest
from types import SimpleNamespace

from app.engine import classify, extract, extract_entities, fingerprint, rank_documents, relevant_passage, short_summary


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

    def test_retrieval_passage_is_bounded_and_centers_matching_evidence(self):
        content = "background " * 100 + "quantum result " + "conclusion " * 100
        passage = relevant_passage(content, "quantum", limit=100)
        self.assertLessEqual(len(passage), 102)
        self.assertIn("quantum", passage)

    def test_entity_candidates_retain_evidence_and_rule_confidence(self):
        entities = extract_entities("OpenAI published a ChatGPT report for Example University.")
        by_name = {entity["name"]: entity for entity in entities}
        self.assertEqual(by_name["OpenAI"]["kind"], "organization")
        self.assertEqual(by_name["ChatGPT"]["kind"], "technology")
        self.assertIn("Example University", by_name)
        self.assertIn("confidence", by_name["OpenAI"])
        self.assertTrue(by_name["OpenAI"]["evidence"])


if __name__ == "__main__":
    unittest.main()
