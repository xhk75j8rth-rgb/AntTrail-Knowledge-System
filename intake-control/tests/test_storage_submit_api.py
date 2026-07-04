from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import storage_config
import submit_card
import server.chat_api as chat_api
from server.chat_api import app
from submit_card import build_submit_ingest_payload


class MockLucasDatabase:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                owner.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "x_api_token": self.headers.get("X-API-Token"),
                    "json": json.loads(body),
                })
                payload = json.dumps({
                    "ok": True,
                    "card_id": "card_manual_1",
                    "node_id": "node_manual_1",
                    "path": "/知识卡/手动文本/demo",
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args) -> None:
                return

        return Handler

    def __enter__(self) -> "MockLucasDatabase":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()


class MockBrainProbe:
    def __init__(self, expected_token: str = "test-lucas-db-key") -> None:
        self.expected_token = expected_token
        self.requests: list[dict] = []
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                owner.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "x_api_token": self.headers.get("X-API-Token"),
                    "json": json.loads(body),
                })
                authorized = self.headers.get("Authorization") == f"Bearer {owner.expected_token}"
                payload = json.dumps({"detail": "schema rejected"}).encode("utf-8")
                self.send_response(422 if authorized else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args) -> None:
                return

        return Handler

    def __enter__(self) -> "MockBrainProbe":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()


class MockSiyuanProbe:
    def __init__(self, expected_token: str = "test-siyuan-token") -> None:
        self.expected_token = expected_token
        self.requests: list[dict] = []
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                owner.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "json": json.loads(body or "{}"),
                })
                authorized = self.headers.get("Authorization") == f"Token {owner.expected_token}"
                payload = json.dumps(
                    {"code": 0, "data": {"notebooks": []}}
                    if authorized
                    else {"code": -1, "msg": "forbidden"}
                ).encode("utf-8")
                self.send_response(200 if authorized else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args) -> None:
                return

        return Handler

    def __enter__(self) -> "MockSiyuanProbe":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()


class StorageSubmitApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "storage.local.json"
        self.env_path = Path(self.tmpdir.name) / ".env"
        self.pipeline_path = Path(self.tmpdir.name) / "link_pipeline.json"
        self.patches = [
            patch.object(storage_config, "CONFIG_PATH", self.config_path),
            patch.object(storage_config, "ENV_PATH", self.env_path),
            patch.object(storage_config, "LINK_PIPELINE_CONFIG_PATH", self.pipeline_path),
            patch.object(submit_card, "resolve_lucas_database_runtime", storage_config.resolve_lucas_database_runtime),
            patch.object(chat_api, "get_public_storage_config", storage_config.get_public_storage_config),
            patch.object(chat_api, "save_storage_config", storage_config.save_storage_config),
            patch.object(chat_api, "test_storage_connection", storage_config.test_storage_connection),
        ]
        for patcher in self.patches:
            patcher.start()
        self._old_env = {
            "LUCAS_DB_API_KEY": os.environ.get("LUCAS_DB_API_KEY"),
            "LUCAS_DB_API_KEY_ARCHIVE": os.environ.get("LUCAS_DB_API_KEY_ARCHIVE"),
            "SIYUAN_TOKEN": os.environ.get("SIYUAN_TOKEN"),
            "CUSTOM_STORAGE_API_KEY": os.environ.get("CUSTOM_STORAGE_API_KEY"),
        }
        for key in self._old_env:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmpdir.cleanup()

    def test_storage_config_saves_key_to_env_without_public_leak(self) -> None:
        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "lucas_database",
            "base_url": "http://127.0.0.1:8768",
            "api_key": "lucas_db_secret_test",
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["provider"]["api_key_present"])
        self.assertNotIn("lucas_db_secret_test", json.dumps(payload, ensure_ascii=False))
        self.assertIn("LUCAS_DB_API_KEY=lucas_db_secret_test", self.env_path.read_text(encoding="utf-8"))

    def test_storage_config_saves_multiple_database_targets_without_public_key_leak(self) -> None:
        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "lucas_database",
            "base_url": "http://127.0.0.1:8765",
            "storage_write_enabled": True,
            "storage_targets": ["siyuan", "lucas_database"],
            "database_targets": [
                {
                    "id": "main",
                    "label": "主数据库",
                    "base_url": "http://127.0.0.1:8765",
                    "web_url": "http://127.0.0.1:5173/",
                    "endpoint": "/api/cards/ingest",
                    "api_key": "main-db-secret",
                    "api_key_env": "LUCAS_DB_API_KEY",
                    "selected": True,
                },
                {
                    "id": "archive",
                    "label": "测试数据库",
                    "base_url": "http://127.0.0.1:28765",
                    "web_url": "http://127.0.0.1:25173/",
                    "endpoint": "/api/cards/ingest",
                    "api_key": "archive-db-secret",
                    "api_key_env": "LUCAS_DB_API_KEY_ARCHIVE",
                    "selected": True,
                },
            ],
            "selected_database_target_ids": ["main", "archive"],
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload["auto_ingest_enabled"])
        self.assertEqual(payload["storage_targets"], ["siyuan", "lucas_database"])
        self.assertEqual(payload["selected_database_target_ids"], ["main", "archive"])
        self.assertEqual(len(payload["database_targets"]), 2)
        self.assertEqual(payload["database_targets"][0]["web_url"], "http://127.0.0.1:5173/")
        self.assertEqual(payload["database_targets"][1]["web_url"], "http://127.0.0.1:25173/")
        self.assertNotIn("main-db-secret", serialized)
        self.assertNotIn("archive-db-secret", serialized)
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("LUCAS_DB_API_KEY=main-db-secret", env_text)
        self.assertIn("LUCAS_DB_API_KEY_ARCHIVE=archive-db-secret", env_text)
        pipeline = json.loads(self.pipeline_path.read_text(encoding="utf-8"))
        self.assertTrue(pipeline["enable_storage_write"])
        self.assertEqual(pipeline["storage_targets"], ["siyuan", "lucas_database"])
        self.assertTrue(pipeline["enable_lucas_database_write"])

    def test_storage_config_can_disable_all_writes_without_deleting_database_targets(self) -> None:
        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "lucas_database",
            "base_url": "http://127.0.0.1:8765",
            "storage_write_enabled": False,
            "storage_targets": ["lucas_database"],
            "database_targets": [{
                "id": "main",
                "label": "主数据库",
                "base_url": "http://127.0.0.1:8765",
                "endpoint": "/api/cards/ingest",
                "selected": True,
            }],
            "selected_database_target_ids": ["main"],
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["auto_ingest_enabled"])
        self.assertEqual(payload["storage_targets"], [])
        self.assertEqual(payload["selected_database_target_ids"], ["main"])
        self.assertEqual(payload["database_targets"][0]["id"], "main")
        pipeline = json.loads(self.pipeline_path.read_text(encoding="utf-8"))
        self.assertFalse(pipeline["enable_storage_write"])
        self.assertFalse(pipeline["enable_lucas_database_write"])

    def test_storage_config_lists_custom_provider_and_writable_targets(self) -> None:
        self.env_path.write_text("LUCAS_DB_API_KEY=db-key\nSIYUAN_TOKEN=siyuan-key\n", encoding="utf-8")
        self.config_path.write_text(json.dumps({
            "active_provider": "lucas_database",
            "providers": {
                "lucas_database": {"base_url": "http://127.0.0.1:8765", "endpoint": "/api/cards/ingest"},
                "siyuan": {"base_url": "http://127.0.0.1:6806", "endpoint": "/api/filetree/createDocWithMd"},
            },
        }, ensure_ascii=False), encoding="utf-8")
        self.pipeline_path.write_text(json.dumps({
            "storage_targets": ["siyuan", "lucas_database"],
            "lucas_database_write_policy": "all_cards",
        }, ensure_ascii=False), encoding="utf-8")

        client = TestClient(app)
        response = client.get("/api/storage/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        provider_ids = {item["id"] for item in payload["presets"]}
        self.assertIn("custom_http", provider_ids)
        self.assertEqual(payload["storage_targets"], ["siyuan", "lucas_database"])
        writable = {item["target_id"] for item in payload["writable_targets"]}
        self.assertEqual(writable, {"siyuan", "lucas_database"})
        statuses = {item["target_id"]: item for item in payload["target_options"]}
        self.assertTrue(statuses["siyuan"]["can_write"])
        self.assertTrue(statuses["lucas_database"]["can_write"])
        self.assertNotIn("db-key", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("siyuan-key", json.dumps(payload, ensure_ascii=False))

    def test_custom_storage_provider_saves_endpoint_and_drives_database_runtime(self) -> None:
        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "custom",
            "base_url": "https://storage.example.com",
            "endpoint": "/v1/cards",
            "api_key": "custom-secret-test",
            "storage_targets": ["lucas_database"],
            "auto_ingest_enabled": True,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active_provider"], "custom_http")
        self.assertEqual(payload["provider"]["endpoint"], "/v1/cards")
        self.assertTrue(payload["provider"]["api_key_present"])
        self.assertIn("CUSTOM_STORAGE_API_KEY=custom-secret-test", self.env_path.read_text(encoding="utf-8"))
        self.assertNotIn("custom-secret-test", json.dumps(payload, ensure_ascii=False))

        runtime = storage_config.resolve_lucas_database_runtime()
        self.assertEqual(runtime["base_url"], "https://storage.example.com")
        self.assertEqual(runtime["endpoint"], "/v1/cards")
        self.assertEqual(runtime["api_key_env"], "CUSTOM_STORAGE_API_KEY")

    def test_storage_config_saves_targets_to_link_pipeline(self) -> None:
        self.pipeline_path.write_text(json.dumps({
            "siyuan_base_url": "http://127.0.0.1:6806",
            "enable_lucas_database_write": False,
        }, ensure_ascii=False), encoding="utf-8")

        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "siyuan",
            "base_url": "http://127.0.0.1:6807",
            "storage_targets": ["siyuan"],
            "auto_ingest_enabled": True,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["storage_targets"], ["siyuan"])
        pipeline = json.loads(self.pipeline_path.read_text(encoding="utf-8"))
        self.assertEqual(pipeline["siyuan_base_url"], "http://127.0.0.1:6807")
        self.assertEqual(pipeline["storage_targets"], ["siyuan"])
        self.assertFalse(pipeline["enable_lucas_database_write"])

        response = client.post("/api/storage/config", json={
            "provider_id": "lucas_database",
            "base_url": "http://127.0.0.1:8765",
            "storage_targets": ["siyuan", "lucas_database"],
            "auto_ingest_enabled": True,
        })

        self.assertEqual(response.status_code, 200)
        pipeline = json.loads(self.pipeline_path.read_text(encoding="utf-8"))
        self.assertEqual(pipeline["storage_targets"], ["siyuan", "lucas_database"])
        self.assertTrue(pipeline["enable_lucas_database_write"])

    def test_storage_config_can_disable_external_writes(self) -> None:
        self.pipeline_path.write_text(json.dumps({
            "storage_targets": ["siyuan", "lucas_database"],
            "enable_lucas_database_write": True,
        }, ensure_ascii=False), encoding="utf-8")

        client = TestClient(app)
        response = client.post("/api/storage/config", json={
            "provider_id": "lucas_database",
            "base_url": "http://127.0.0.1:8765",
            "storage_targets": [],
            "auto_ingest_enabled": False,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["storage_targets"], [])
        self.assertFalse(payload["auto_ingest_enabled"])
        pipeline = json.loads(self.pipeline_path.read_text(encoding="utf-8"))
        self.assertEqual(pipeline["storage_targets"], [])
        self.assertFalse(pipeline["enable_lucas_database_write"])

    def test_storage_test_brain_schema_rejection_counts_as_connected(self) -> None:
        with MockBrainProbe() as mock_db:
            self.env_path.write_text("LUCAS_DB_API_KEY=test-lucas-db-key\n", encoding="utf-8")
            self.config_path.write_text(json.dumps({
                "active_provider": "lucas_database",
                "providers": {
                    "lucas_database": {
                        "base_url": mock_db.base_url,
                        "endpoint": "/api/cards/ingest",
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")

            client = TestClient(app)
            response = client.post("/api/storage/test", json={"provider_id": "lucas_database"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "connected_schema_rejected")
            self.assertFalse(payload["write_attempted"])
            self.assertNotIn("test-lucas-db-key", json.dumps(payload, ensure_ascii=False))
            self.assertEqual(len(mock_db.requests), 1)
            self.assertEqual(mock_db.requests[0]["path"], "/api/cards/ingest")
            self.assertTrue(mock_db.requests[0]["json"]["connection_probe"])

    def test_storage_test_siyuan_uses_notebook_list_without_write(self) -> None:
        with MockSiyuanProbe() as mock_siyuan:
            self.env_path.write_text("SIYUAN_TOKEN=test-siyuan-token\n", encoding="utf-8")
            self.config_path.write_text(json.dumps({
                "active_provider": "siyuan",
                "providers": {
                    "siyuan": {
                        "base_url": mock_siyuan.base_url,
                        "endpoint": "/api/filetree/createDocWithMd",
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")

            client = TestClient(app)
            response = client.post("/api/storage/test", json={"provider_id": "siyuan"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "connected")
            self.assertEqual(payload["stage"], "notebook_list")
            self.assertFalse(payload["write_attempted"])
            self.assertNotIn("test-siyuan-token", json.dumps(payload, ensure_ascii=False))
            self.assertEqual(mock_siyuan.requests[0]["path"], "/api/notebook/lsNotebooks")

    def test_build_submit_payload_creates_temporary_manual_card_v1(self) -> None:
        payload = build_submit_ingest_payload({
            "title": "demo title",
            "content": "form body",
            "tags": "测试;手动文本",
        })

        self.assertEqual(payload["card"]["schema_name"], "ComposedCardV1")
        self.assertEqual(payload["card"]["schema_version"], "1")
        self.assertEqual(payload["card"]["card_type"], "temporary_card")
        self.assertEqual(payload["card"]["composer_status"], "not_run")
        self.assertFalse(payload["quality_gate"]["passed"])
        self.assertIn("manual_text_bypasses_source_reader_model_composer_quality_gate", payload["quality_gate"]["errors"])
        self.assertIn("markdown", payload["rendered_views"])

    def test_submit_posts_manual_text_card_to_lucas_database(self) -> None:
        with MockLucasDatabase() as mock_db:
            self.env_path.write_text("LUCAS_DB_API_KEY=test-lucas-db-key\n", encoding="utf-8")
            self.config_path.write_text(json.dumps({
                "active_provider": "lucas_database",
                "providers": {
                    "lucas_database": {
                        "base_url": mock_db.base_url,
                        "endpoint": "/api/cards/ingest",
                    },
                },
            }, ensure_ascii=False), encoding="utf-8")

            client = TestClient(app)
            response = client.post("/api/submit", json={
                "title": "手动文本测试",
                "content": "这是一条手动文本内容。",
                "tags": ["手动文本"],
            })

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["card_id"], "card_manual_1")
            self.assertEqual(payload["card"]["schema_name"], "ComposedCardV1")
            self.assertEqual(payload["card"]["card_type"], "temporary_card")
            self.assertEqual(len(mock_db.requests), 1)
            request = mock_db.requests[0]
            self.assertEqual(request["path"], "/api/cards/ingest")
            self.assertEqual(request["authorization"], "Bearer test-lucas-db-key")
            self.assertEqual(request["json"]["card"]["schema_name"], "ComposedCardV1")
            self.assertEqual(request["json"]["card"]["card_type"], "temporary_card")
            self.assertFalse(request["json"]["quality_gate"]["passed"])
            self.assertNotIn("test-lucas-db-key", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
