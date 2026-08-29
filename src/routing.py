"""
Free API Hub — 智能路由层（ADR-008 别名层 / ADR-009 能力打分选路）

叠加式、非破坏性：
  - 仅当 model 命中已配置 alias 时驱动新选路；
  - 其余情况（空 / 未知 / 裸下游模型名，如 opencode 的 "free-api-hub"）保持现状全池 failover。
  - 选路只决定 call_api 外层遍历 provider 的顺序与过滤，既有重试/熔断/429/5xx 内核不变。
"""
import logging

logger = logging.getLogger(__name__)


class Router:
    """按 alias 标签筛选候选池，并按策略（priority/capability/latency）排序。"""

    VALID_STRATEGIES = ("priority", "capability", "latency")

    def __init__(self, routing_cfg, providers):
        routing_cfg = routing_cfg or {}
        self.enabled = bool(routing_cfg.get("enabled", True))
        self.default_strategy = routing_cfg.get("default_strategy", "priority")
        if self.default_strategy not in self.VALID_STRATEGIES:
            logger.warning(f"routing.default_strategy 非法值 {self.default_strategy!r}，回退 priority")
            self.default_strategy = "priority"
        self.hide_raw = bool(routing_cfg.get("hide_raw", False))

        # ADR-010 manual_override：可填 provider 名 / 裸模型ID / alias 名，空则停用
        self.manual_override = (routing_cfg.get("manual_override") or "").strip()

        self.aliases = {}
        for a in (routing_cfg.get("aliases") or []):
            name = a.get("name")
            if not name:
                continue
            strat = a.get("strategy", self.default_strategy)
            if strat not in self.VALID_STRATEGIES:
                strat = self.default_strategy
            self.aliases[name] = {
                "tags": a.get("tags", []) or [],
                "strategy": strat,
            }

        self._caps = {}
        for p in providers:
            self._caps[p["name"]] = self._normalize_caps(p.get("capabilities"))

        # 加载期校验：alias 标签需有 provider 交集，否则告警并运行时退化为全池
        for an, a in self.aliases.items():
            if a["tags"] and not any(
                set(self._caps.get(pn, {}).get("tags", [])) & set(a["tags"])
                for pn in self._caps
            ):
                logger.warning(f"alias {an!r} 标签 {a['tags']} 无 provider 匹配，将退化为全池")

        self._latency = {}  # name -> EWMA 秒

    def _resolve_manual_override(self, available):
        """ADR-010：manual_override 钉死单 provider（> alias > priority）。

        匹配顺序：provider 名 → 裸模型ID → alias 名。
        未命中 → 返回 None（调用方退化为全池并告警）。
        """
        mo = self.manual_override
        if not mo:
            return None
        for p in available:
            if p.get("name") == mo:
                return p
        for p in available:
            if p.get("model") == mo:
                return p
        if mo in self.aliases:
            tags = set(self.aliases[mo]["tags"])
            for p in available:
                if tags and set(self._caps.get(p["name"], {}).get("tags", [])) & tags:
                    return p
        return None

    @staticmethod
    def _normalize_caps(c):
        c = c or {}
        return {
            "context_window": int(c.get("context_window", 32768)),
            "output_limit": int(c.get("output_limit", 4096)),
            "supports_tools": bool(c.get("supports_tools", False)),
            "tags": c.get("tags", []) or [],
        }

    def set_latency(self, name, ewma):
        self._latency[name] = ewma

    def alias_entries(self):
        """/v1/models 中展示的别名条目（OpenAI 兼容 shape）"""
        return [
            {"id": an, "provider": "*", "display": "Free API Hub 别名",
             "priority": 0, "alias": True}
            for an in self.aliases
        ]

    def resolve(self, model, available, ctx=None):
        """返回 call_api 应遍历的 provider 有序列表。

        available: 已通过 breaker/creds 过滤的 provider 列表（priority 顺序）。
        仲裁链（ADR-010）：manual_override > 显式命中 alias > priority/capability > 全池。
        非 alias（含 opencode 的 "free-api-hub"）→ 无 override 时原样返回 available。
        """
        ctx = ctx or {}
        if not self.enabled or not available:
            return available

        mo = self._resolve_manual_override(available)
        if mo is not None:
            logger.info(f"manual_override 命中 {mo['name']}，钉死单 provider")
            return [mo]
        if self.manual_override:
            logger.warning(
                f"manual_override {self.manual_override!r} 未命中任何可用 provider，退化为全池"
            )

        if not model or model not in self.aliases:
            return available

        alias = self.aliases[model]
        tags = set(alias["tags"])
        if tags:
            pool = [p for p in available
                    if set(self._caps.get(p["name"], {}).get("tags", [])) & tags]
        else:
            pool = list(available)
        if not pool:
            logger.warning(f"alias {model!r} 候选池为空，退化为全池（保持现状顺序）")
            return available
        return self.select(pool, alias["strategy"], ctx)

    def select(self, pool, strategy, ctx=None):
        ctx = ctx or {}
        if strategy == "latency":
            return sorted(pool, key=lambda p: self._latency.get(p["name"], 1e9))
        if strategy == "capability":
            est = ctx.get("est_tokens", 0)
            tools = ctx.get("tools", False)

            def score(p):
                c = self._caps.get(p["name"], {})
                s = 0.0
                if tools and c.get("supports_tools"):
                    s += 100.0
                cw = c.get("context_window", 32768)
                if est and cw < est * 1.2:
                    s -= 1000.0  # 上下文不足，强惩罚
                s += min(cw, 200000) / 1000.0
                s += (1.0 / max(p.get("priority", 99), 1)) * 10.0
                return s

            return sorted(pool, key=score, reverse=True)
        # priority（默认）
        return sorted(pool, key=lambda p: p.get("priority", 99))
