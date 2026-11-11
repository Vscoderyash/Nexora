import hashlib
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main, storage


class PrivateApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        cls.session_factory = sessionmaker(bind=cls.engine, autoflush=False, expire_on_commit=False)
        cls.previous_engine = storage.engine
        cls.previous_sessions = main.SessionLocal
        storage.engine = cls.engine
        main.SessionLocal = cls.session_factory
        cls.client_context = TestClient(main.app)
        cls.client = cls.client_context.__enter__()
        cls.admin_token = "nxr_test_admin_secret_0123456789"
        cls.reader_token = "nxr_test_reader_secret_0123456789"
        with cls.session_factory() as db:
            db.add_all([
                storage.ApiKey(id="admin", name="test admin", key_hash=hashlib.sha256(cls.admin_token.encode()).hexdigest(), permissions=["admin", "read", "write"]),
                storage.ApiKey(id="reader", name="test reader", key_hash=hashlib.sha256(cls.reader_token.encode()).hexdigest(), permissions=["read"]),
            ])
            source = storage.Source(id="source-1", name="Test source", url="https://example.org/feed", kind="rss", enabled=False)
            db.add(source)
            db.flush()
            db.add(storage.Document(id="doc-1", source_id=source.id, url="https://example.org/quantum", title="Quantum computing progress",
                                    publisher="Example Institute", content="Quantum computing hardware research advances with stable qubits.",
                                    summary="Research on quantum computing hardware and qubit stability.", fingerprint="a" * 64,
                                    categories=["Science"], quality={"source_reliability": "unrated"}, status="processed"))
            db.add(storage.Entity(id="entity-1", name="OpenAI", normalized_name="openai", kind="organization"))
            db.add(storage.DocumentEntity(document_id="doc-1", entity_id="entity-1", confidence=0.95, evidence="OpenAI"))
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        main.SessionLocal = cls.previous_sessions
        storage.engine = cls.previous_engine
        cls.engine.dispose()

    def test_private_routes_reject_missing_or_invalid_keys(self):
        self.assertEqual(self.client.get("/v1/system/status").status_code, 401)
        self.assertEqual(self.client.get("/v1/system/status", headers={"Authorization": "Bearer invalid"}).status_code, 401)

    def test_root_serves_the_repository_level_console(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("NEXORA · Knowledge Engine", response.text)

    def test_reader_can_search_and_retrieve_stored_document(self):
        headers = {"Authorization": f"Bearer {self.reader_token}"}
        result = self.client.post("/v1/knowledge/query", headers=headers, json={"query": "quantum computing"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["results"][0]["document_id"], "doc-1")
        document = self.client.get("/v1/knowledge/documents/doc-1", headers=headers)
        self.assertEqual(document.status_code, 200)
        self.assertIn("Source", document.json()["publisher"])

    def test_read_only_key_can_view_overview_but_cannot_manage_sources(self):
        response = self.client.get("/v1/admin/overview", headers={"Authorization": f"Bearer {self.reader_token}"})
        self.assertEqual(response.status_code, 200, response.text)
        restricted = self.client.get("/v1/admin/sources", headers={"Authorization": f"Bearer {self.reader_token}"})
        self.assertEqual(restricted.status_code, 403)

    def test_entity_retrieval_returns_source_linked_documents(self):
        headers = {"Authorization": f"Bearer {self.reader_token}"}
        response = self.client.get("/v1/knowledge/entities/entity-1", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["documents"][0]["document_id"], "doc-1")
        self.assertEqual(response.json()["documents"][0]["evidence"], "OpenAI")

    def test_admin_can_reindex_documents_into_the_entity_graph(self):
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        response = self.client.post("/v1/admin/entities/reindex?limit=20", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json()["entity_mentions"], 1)
        graph = self.client.get("/v1/admin/graph", headers=headers)
        self.assertEqual(graph.status_code, 200)
        self.assertTrue(any(node["type"] == "entity" for node in graph.json()["nodes"]))

    def test_revoked_key_is_denied(self):
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        create = self.client.post("/v1/admin/keys", headers=headers, json={"name": "temporary", "permissions": ["read"]})
        self.assertEqual(create.status_code, 201, create.text)
        key = create.json()
        self.assertTrue(key["shown_once"])
        revoke = self.client.delete(f"/v1/admin/keys/{key['id']}", headers=headers)
        self.assertEqual(revoke.status_code, 200)
        denied = self.client.get("/v1/account/usage", headers={"Authorization": f"Bearer {key['secret']}"})
        self.assertEqual(denied.status_code, 401)


if __name__ == "__main__":
    unittest.main()
