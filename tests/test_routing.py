"""
Free API Hub — 智能路由单元测试（ADR-008/009）

纯单元：直接测 Router 解析/打分逻辑；并通过临时配置构建 APIGateway 验证
list_models 别名展示与 _resolve_route 对 opencode 占位 model 的零回归。
无需 5080 实例在线。
"""
import os
import sys
import tempfile
import unittest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from routing import Router
from gateway import APIGateway


PROVIDERS = [
    {"name": "p1", "model": "m1", "priority": 1,
     "capabilities": {"context_window": 128000, "supports_tools": True, "tags": ["chat"]}},
    {"name": "p2", "model": "m2", "priority": 2,
     "capabilities": {"context_window": 8000, "supports_tools": False, "tags": ["chat", "lightweight"]}},
    {"name": "p3", "model": "m3", "priority": 3,
     "capabilities": {"context_window": 32000, "supports_tools": False, "tags": ["code"]}},
]

ROUTING = {
    "enabled": True,
    "default_strategy": "priority",
    "aliases": [
        {"name": "fah/chat", "tags": ["chat"], "strategy": "capability"},
        {"name": "fah/lat", "tags": ["lightweight"], "strategy": "latency"},
        {"name": "fah/all", "tags": [], "strategy": "latency"},
        {"name": "fah/empty", "tags": ["nonexistent"], "strategy": "capability"},
    ],
}


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.r = Router(ROUTING, PROVIDERS)
        self.available = list(PROVIDERS)  # priority 顺序 p1,p2,p3

    def test_default_model_keeps_current_behavior(self):
        # opencode 占位 model "free-api-hub" / 未知 / 空 → 原样全池
        for m in ("free-api-hub", "unknown", None, ""):
            out = self.r.resolve(m, self.available)
            self.assertEqual([p["name"] for p in out], ["p1", "p2", "p3"], msg=f"model={m!r}")

    def test_alias_tag_filter(self):
        out = self.r.resolve("fah/chat", self.available)
        self.assertEqual([p["name"] for p in out], ["p1", "p2"])  # p3 无 chat 标签
        out2 = self.r.resolve("fah/lat", self.available)
        self.assertEqual([p["name"] for p in out2], ["p2"])  # 仅 lightweight

    def test_capability_prefers_tools_and_context(self):
        out = self.r.resolve("fah/chat", self.available, ctx={"tools": True, "est_tokens": 0})
        self.assertEqual(out[0]["name"], "p1")  # p1 支持 tools + 大窗口
        # 超长 prompt：p2(8k) 上下文不足应被惩罚，p1 仍优先
        out2 = self.r.resolve("fah/chat", self.available, ctx={"tools": True, "est_tokens": 100000})
        self.assertEqual(out2[0]["name"], "p1")

    def test_latency_ordering(self):
        self.r.set_latency("p1", 5.0)
        self.r.set_latency("p2", 0.1)
        self.r.set_latency("p3", 1.0)
        out = self.r.resolve("fah/all", self.available)
        self.assertEqual([p["name"] for p in out], ["p2", "p3", "p1"])

    def test_empty_pool_degrades_to_full(self):
        out = self.r.resolve("fah/empty", self.available)
        self.assertEqual([p["name"] for p in out], ["p1", "p2", "p3"])

    def test_alias_entries_shape(self):
        entries = self.r.alias_entries()
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(e["provider"] == "*" and "alias" in e for e in entries))


class TestGatewayRouting(unittest.TestCase):
    def setUp(self):
        os.environ["TEST_FAH_KEY"] = "dummy"
        cfg = {
            "gateway": {
                "retry_seconds": 60,
                "routing": {
                    "enabled": True,
                    "default_strategy": "priority",
                    "aliases": [{"name": "fah/chat-free", "tags": ["chat"], "strategy": "capability"}],
                },
            },
            "providers": [
                {"name": "a", "model": "ma", "priority": 1, "api_key": "${TEST_FAH_KEY}",
                 "capabilities": {"context_window": 128000, "supports_tools": True, "tags": ["chat"]}},
                {"name": "b", "model": "mb", "priority": 2, "api_key": "${TEST_FAH_KEY}",
                 "capabilities": {"context_window": 32000, "supports_tools": False, "tags": ["code"]}},
            ],
        }
        self.tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        yaml.safe_dump(cfg, self.tmp)
        self.tmp.close()
        self.gw = APIGateway(config_path=self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_router_built(self):
        self.assertIsNotNone(self.gw._router)
        self.assertTrue(self.gw._router.enabled)

    def test_list_models_includes_aliases_and_raw(self):
        data = self.gw.list_models()["data"]
        ids = [m["id"] for m in data]
        self.assertIn("fah/chat-free", ids)      # 别名
        self.assertIn("ma", ids)                  # 裸模型仍保留（opencode 不受影响）
        self.assertIn("mb", ids)

    def test_opencode_placeholder_model_zero_regression(self):
        available = self.gw.get_available_providers()
        out = self.gw._resolve_route("free-api-hub", available, [{"role": "user", "content": "hi"}], {})
        self.assertEqual([p["name"] for p in out], ["a", "b"])  # 全池 failover 现状


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = res.testsRun - len(res.failures) - len(res.errors)
    print(f"\n结果: {passed} 通过, {len(res.failures) + len(res.errors)} 失败, 0 跳过")
    sys.exit(0 if res.wasSuccessful() else 1)
