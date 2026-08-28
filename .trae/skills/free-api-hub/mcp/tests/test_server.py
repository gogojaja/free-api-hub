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
        os.environ["OPENCE_AUDIT"] = os.path.join(self.tmp, "security_audit.csv")

    def tearDown(self):
        os.environ.pop("OPENCE_HOME", None)
        os.environ.pop("OPENCE_CONFIG", None)
        os.environ.pop("OPENCE_AUDIT", None)

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
        os.environ["OPENCE_AUDIT"] = os.path.join(self.tmp, "security_audit.csv")
        Path(os.environ["OPENCE_CONFIG"]).write_text(json.dumps({"provider": {}}), encoding="utf-8")

    def tearDown(self):
        os.environ.pop("OPENCE_HOME", None)
        os.environ.pop("OPENCE_CONFIG", None)
        os.environ.pop("OPENCE_AUDIT", None)

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

    def test_add_provider_rejects_existing(self):
        T.add_provider("dupprov", "https://api.x.com/v1", "m1")
        res = T.add_provider("dupprov", "https://api.y.com/v1", "m2")
        self.assertFalse(res["ok"])
        cfg = T.load_config()
        self.assertEqual(cfg["provider"]["dupprov"]["options"]["baseURL"], "https://api.x.com/v1")

    def test_add_provider_rejects_protected(self):
        res = T.add_provider("opencode", "https://opencode.ai/zen/v1", "big-pickle")
        self.assertFalse(res["ok"])
        self.assertIn("保护区", res["error"])

    def test_add_provider_preserves_comments(self):
        raw = (
            '{\n'
            '  // 顶层注释：手工调优配置，勿删\n'
            '  "provider": {\n'
            '    /* 块注释 */\n'
            '    "zhipu": {\n'
            '      "options": {"baseURL": "https://z.example.com/v1"}\n'
            '    }\n'
            '    // 尾部注释\n'
            '  }\n'
            '}\n'
        )
        Path(os.environ["OPENCE_CONFIG"]).write_text(raw, encoding="utf-8")
        res = T.add_provider("newprov", "https://api.new.com/v1", "m1")
        self.assertTrue(res["ok"])
        self.assertEqual(res["write_mode"], "preserved")
        new_raw = Path(os.environ["OPENCE_CONFIG"]).read_text(encoding="utf-8")
        # 注释全部保留
        self.assertIn("// 顶层注释：手工调优配置，勿删", new_raw)
        self.assertIn("/* 块注释 */", new_raw)
        self.assertIn("// 尾部注释", new_raw)
        # 新条目已写入且原条目未被改动
        cfg = T.load_config()
        self.assertIn("newprov", cfg["provider"])
        self.assertEqual(cfg["provider"]["zhipu"]["options"]["baseURL"], "https://z.example.com/v1")


class TestCatalog(unittest.TestCase):
    def test_catalog_search(self):
        res = T.catalog_search("glm")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["count"], 1)


class TestModelLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["OPENCE_HOME"] = self.tmp
        os.environ["OPENCE_CONFIG"] = os.path.join(self.tmp, "opencode.jsonc")
        os.environ["OPENCE_AUDIT"] = os.path.join(self.tmp, "security_audit.csv")

    def tearDown(self):
        os.environ.pop("OPENCE_HOME", None)
        os.environ.pop("OPENCE_CONFIG", None)
        os.environ.pop("OPENCE_AUDIT", None)

    def _write_cfg(self, raw):
        Path(os.environ["OPENCE_CONFIG"]).write_text(raw, encoding="utf-8")

    def test_toml_limit_parse(self):
        text = (
            'name = "big-pickle"\n'
            '[limit]\n'
            'context = 200000\n'
            'output = 32000\n'
            '[tag]\nkind = "zen"\n'
        )
        lim = T._toml_limit(text)
        self.assertEqual(lim, {"context": 200000, "output": 32000})
        self.assertIsNone(T._toml_limit("# no limit block"))

    def test_check_model_limits(self):
        self._write_cfg(json.dumps({
            "provider": {"demo": {"models": {
                "m-a": {"limit": {"context": 9999, "output": 128}},
                "m-b": {"limit": {"context": 131072, "output": 8192}},
                "m-no": {},
            }}},
        }))
        fake = {
            "m-a": ({"context": 200000, "output": 32000}, "models.dev/demo"),
            "m-b": ({"context": 131072, "output": 8192}, "models.dev/demo"),
            "m-no": (None, None),
        }
        orig = T._official_limits
        T._official_limits = lambda p, m, d="": fake.get(m, (None, None))
        try:
            res = T.check_model_limits("demo")
        finally:
            T._official_limits = orig
        self.assertTrue(res["ok"])
        self.assertEqual(res["summary"], {"match": 1, "diff": 1, "no_official": 1})
        by = {r["model"]: r for r in res["rows"]}
        self.assertEqual(by["m-a"]["status"], "diff")
        self.assertEqual(by["m-b"]["status"], "match")
        self.assertEqual(by["m-no"]["status"], "no_official")

    def test_update_model_limit_preserves_comments(self):
        raw = (
            '{\n'
            '  // 手工调优配置，勿删\n'
            '  "provider": {\n'
            '    "demo": {\n'
            '      "models": {\n'
            '        "m-a": {\n'
            '          "name": "m-a",\n'
            '          "limit": { "context": 9999, "output": 128 }\n'
            '        }\n'
            '      }\n'
            '    }\n'
            '  }\n'
            '}\n'
        )
        self._write_cfg(raw)
        res = T.update_model_limit("demo", "m-a", context=200000, output=32000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "preserved")
        self.assertTrue(res["backup"])
        self.assertTrue(Path(res["backup"]).exists())
        new_raw = Path(os.environ["OPENCE_CONFIG"]).read_text(encoding="utf-8")
        self.assertIn("// 手工调优配置，勿删", new_raw)
        cfg = T.load_config()
        self.assertEqual(cfg["provider"]["demo"]["models"]["m-a"]["limit"],
                         {"context": 200000, "output": 32000})

    def test_update_model_limit_validates(self):
        self._write_cfg(json.dumps({"provider": {"demo": {"models": {}}}}))
        res = T.update_model_limit("Bad Name", "m-a", context=1)
        self.assertFalse(res["ok"])
        res = T.update_model_limit("demo", "m-a", context=-5)
        self.assertFalse(res["ok"])
        res = T.update_model_limit("demo", "m-a")
        self.assertFalse(res["ok"])
        res = T.update_model_limit("demo", "m-a", context=1, output=None)
        self.assertFalse(res["ok"])  # m-a 未配置


if __name__ == "__main__":
    unittest.main(verbosity=2)
