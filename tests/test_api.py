import unittest

from fastapi.testclient import TestClient

from app.api import app


class ApiTests(unittest.TestCase):
    def test_discovery_is_read_only(self):
        with TestClient(app) as client:
            response = client.get("/tools")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                {t["name"] for t in response.json()["tools"]},
                {"create_file", "read_file", "append_file"},
            )

    def test_oversized_input_is_rejected(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/run", json={"user_input": "x" * 4001}).status_code, 422)
            self.assertEqual(
                client.post("/run", params={"user_input": "x" * 4001}).status_code, 422
            )

    def test_blank_input_is_rejected(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/run", json={"user_input": "   "}).status_code, 422)
