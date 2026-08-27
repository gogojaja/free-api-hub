#!/usr/bin/env python3
"""free-api-hub MCP 单元测试（标准库 unittest，无需联网）。"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools_impl as T  # noqa: E402
import server  # noqa: E402


class TestSecurity(unittest.TestCase):
    def test_is_safe_url(self):
        self.assertTrue(T.is_safe_url("https://api.siliconflow.cn/v1"))
        self.assertTrue(T.is_safe_url("http://example.com"))
        self.assertFalse(T.is_safe_url("http://127.0.0.1/api"))
        self.assertFalse(T.is_safe_url("http://localhost/x"))
        self.assertFalse(T.is_safe_url("http://169.254.169.254/"))
        self.assertFalse(T.is_safe_url("http://10.0.0.1/"))
        self.assertFalse(T.is_safe_url("http://192.168.1.1/"))
        self.assertFalse(T.is_safe_url("ftp://x"))
        self.assertFalse(T.is_safe_url("https://"))

    def test_validate_provider_name(self):
        self.assertTrue(T.validate_provider_name("openrouter"))
        self.assertTrue(T.validate_provider_name("my-provider-1"))
        self.assertFalse(T.validate_provider_name("Bad_Name"))
        self.assertFalse(T.validate_provider_name("bad name"))


class TestProtocol(unittest.TestCase):
    def test_initialize(self):
        resp = server._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["protocolVersion"], server.PROTOCOL_VERSION)
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_notification_no_response(self):
        self.assertIsNone(server._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_list(self):
        resp = server._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("list_providers", names)
        self.assertIn("set_api_key", names)

    def test_tools_call_unknown(self):
        resp = server._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])

    def test_ping(self):
        resp = server._handle({"jsonrpc": "2.0", "id": 4, "method": "ping"})
        self.assertEqual(resp["result"], {})


class TestKeyVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["OPENCE_HOME"] = self.tmp
        os.environ["OPENCE_CONFIG"] = os.path.join(self.tmp, "opencode.jsonc")

    def tearDown(self):
        os.environ.pop("OPENCE_HOME", None)
        os.environ.pop("OPENCE_CONFIG", None)

    def test_set_api_key_permissions_and_no_plaintext(self):
        res = T.set_api_key("testprov", "SECRET123")
        self.assertTrue(res["ok"])
        key_path = Path(self.tmp) / "testprov-api-key"
        self.assertTrue(key_path.exists())
        mode = oct(key_path.stat().st_mode & 0o777)
        self.assertEqual(mode, "0o600")
        # 返回体不含明文
        self.assertNotIn("SECRET123", json.dumps(res))
        # 文件内容确实是明文（落盘）
        self.assertEqual(key_path.read_text(), "SECRET123")


class TestConfigAppendSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["OPENCE_HOME"] = self.tmp
        os.environ["OPENCE_CONFIG"] = os.path.join(self.tmp, "opencode.jsonc")
        Path(os.environ["OPENCE_CONFIG"]).write_text(json.dumps({"provider": {}}), encoding="utf-8")

    def tearDown(self):
        os.environ.pop("OPENCE_HOME", None)
        os.environ.pop("OPENCE_CONFIG", None)

    def test_add_provider_valid(self):
        res = T.add_provider("newprov", "https://api.newprov.com/v1", "model-a,model-b", "newprov")
        self.assertTrue(res["ok"])
        cfg = T.load_config()
        self.assertIn("newprov", cfg["provider"])
        self.assertTrue(Path(os.environ["OPENCE_CONFIG"] + ".bak").exists() or
                        list(Path(self.tmp).glob("opencode.jsonc.bak.*")))

    def test_add_provider_rejects_ssrf(self):
        res = T.add_provider("bad", "http://127.0.0.1/x", "m")
        self.assertFalse(res["ok"])

    def test_add_provider_rejects_bad_name(self):
        res = T.add_provider("Bad Name", "https://api.x.com/v1", "m")
        self.assertFalse(res["ok"])


class TestCatalog(unittest.TestCase):
    def test_catalog_search(self):
        res = T.catalog_search("glm")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
