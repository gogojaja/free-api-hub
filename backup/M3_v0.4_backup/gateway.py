"""
Free API Hub — 多 Provider 统一网关核心
按优先级调用免费 API，失败自动切换，用量追踪
"""
import os
import json
import logging
import time
import datetime
import hmac
import hashlib
import base64
import re
import shutil
import urllib.parse
import threading
import collections
import requests
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _load_dotenv():
    """从项目根目录的 .env 文件加载环境变量（不覆盖已存在的值）"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _percentile(sorted_values, q):
    """线性插值分位数（NEW-002 直方图）"""
    values = sorted(sorted_values)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    pos = q * (len(values) - 1)
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    frac = pos - low
    return values[low] * (1 - frac) + values[high] * frac


class CircuitBreaker:
    """熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED（失败率熔断，DEGRADE-001）

    触发条件（满足其一即 OPEN）：
      - 连续失败达到 failure_threshold（快速熔断，样本不足时兜底）
      - 1min 滑动窗口失败率 > failure_rate_threshold（样本 >= min_samples）
    差异化冷却：429 短冷却(cooldown_429) / 5xx 长冷却(cooldown_5xx)
    状态流转：CLOSED → OPEN → HALF_OPEN → CLOSED
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold=3, recovery_timeout=60,
                 failure_rate_threshold=0.25, min_samples=10,
                 window_seconds=60, cooldown_429=15, cooldown_5xx=60,
                 half_open_success_threshold=2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_rate_threshold = failure_rate_threshold
        self.min_samples = min_samples
        self.window_seconds = window_seconds
        self.cooldown_429 = cooldown_429
        self.cooldown_5xx = cooldown_5xx
        self.half_open_success_threshold = half_open_success_threshold
        self._states = {}   # name -> {state, failure_count, last_failure_ts, cooldown, half_open_success}
        self._samples = {}  # name -> deque[(ts, is_failure)]
        self._lock = threading.Lock()

    def _prune(self, name, now):
        samples = self._samples.get(name)
        if not samples:
            return
        cutoff = now - self.window_seconds
        while samples and samples[0][0] <= cutoff:
            samples.popleft()

    def _failure_rate(self, name, now):
        """窗口失败率；样本不足 min_samples 时返回 0（避免冷启动误判）"""
        self._prune(name, now)
        samples = self._samples.get(name, ())
        if len(samples) < self.min_samples:
            return 0.0
        failures = sum(1 for _, is_fail in samples if is_fail)
        return failures / len(samples)

    def _cooldown_for(self, error_type):
        if error_type == "429":
            return self.cooldown_429
        if error_type == "5xx":
            return self.cooldown_5xx
        return self.recovery_timeout

    def _get_state(self, name):
        """获取当前状态（含 OPEN→HALF_OPEN 自动转换）"""
        info = self._states.get(name)
        if not info:
            return self.CLOSED
        if info["state"] == self.OPEN:
            cooldown = info.get("cooldown", self.recovery_timeout)
            if time.time() - info["last_failure_ts"] >= cooldown:
                info["state"] = self.HALF_OPEN
                return self.HALF_OPEN
            return self.OPEN
        return info["state"]

    def is_available(self, name):
        """检查 provider 是否可被调用（CLOSED 或 HALF_OPEN 可用）"""
        with self._lock:
            state = self._get_state(name)
            return state != self.OPEN

    def get_state(self, name):
        """获取 provider 的熔断状态（公开接口，线程安全）"""
        with self._lock:
            return self._get_state(name)

    def should_allow(self, name, probe_ratio=0.2):
        """FAILOVER-003：渐进恢复放行判定

        CLOSED → 100% 放行；OPEN → 拒绝；HALF_OPEN → 仅 probe_ratio(20%) 概率放行。
        依赖 get_available_providers 在 call_api 每次请求时判定，实现流量渐进引入。
        """
        with self._lock:
            state = self._get_state(name)
            if state == self.CLOSED:
                return True
            if state == self.OPEN:
                return False
            import random
            return random.random() < probe_ratio

    def record_success(self, name):
        """记录成功：写入窗口样本，并按失败率触发熔断（DEGRADE-001）

        FAILOVER-003 渐进恢复：
          - HALF_OPEN 状态累计连续成功，达 half_open_success_threshold(2) 次即转 CLOSED 全量恢复
          - 窗口失败率仍超阈值 → 重新 OPEN（保守优先）
        """
        with self._lock:
            now = time.time()
            samples = self._samples.setdefault(name, collections.deque())
            samples.append((now, False))
            self._prune(name, now)
            info = self._states.get(name)
            if info is None:
                info = {
                    "state": self.CLOSED,
                    "failure_count": 0,
                    "last_failure_ts": 0,
                    "cooldown": self.recovery_timeout,
                    "half_open_success": 0,
                }
            if self._failure_rate(name, now) > self.failure_rate_threshold:
                info["state"] = self.OPEN
                info["half_open_success"] = 0
            elif self._get_state(name) == self.HALF_OPEN:
                info["half_open_success"] += 1
                if info["half_open_success"] >= self.half_open_success_threshold:
                    info["state"] = self.CLOSED
                    info["half_open_success"] = 0
            else:
                info["state"] = self.CLOSED
            self._states[name] = info

    def record_failure(self, name, error_type=None):
        """记录失败：写入窗口样本，可能按失败率触发熔断

        error_type 决定差异化冷却：
          429 → cooldown_429，5xx → cooldown_5xx，
          None/其他 → recovery_timeout（保持向后兼容）。
        """
        with self._lock:
            now = time.time()
            samples = self._samples.setdefault(name, collections.deque())
            samples.append((now, True))
            self._prune(name, now)
            info = self._states.get(name, {
                "state": self.CLOSED,
                "failure_count": 0,
                "last_failure_ts": 0,
                "cooldown": self.recovery_timeout,
                "half_open_success": 0,
            })
            info["failure_count"] += 1
            info["last_failure_ts"] = now
            info["cooldown"] = self._cooldown_for(error_type)
            if self._get_state(name) == self.HALF_OPEN:
                info["state"] = self.OPEN
                info["failure_count"] = 0
                info["half_open_success"] = 0
            elif info["failure_count"] >= self.failure_threshold:
                info["state"] = self.OPEN
            elif self._failure_rate(name, now) > self.failure_rate_threshold:
                info["state"] = self.OPEN
            self._states[name] = info

    def reset(self, name=None):
        """重置熔断器"""
        with self._lock:
            if name:
                self._states.pop(name, None)
                self._samples.pop(name, None)
            else:
                self._states.clear()
                self._samples.clear()

    def get_detail(self):
        """获取所有 provider 的熔断状态"""
        with self._lock:
            result = {}
            for name, info in self._states.items():
                state = self._get_state(name)
                result[name] = {
                    "state": state,
                    "failure_count": info["failure_count"],
                    "cooldown": info.get("cooldown", self.recovery_timeout),
                }
            return result

    def get_failed_names(self):
        """获取处于 OPEN 状态的 provider 名称列表"""
        with self._lock:
            return [name for name, info in self._states.items()
                    if self._get_state(name) == self.OPEN]


class RateLimiter:
    """滑动窗口限流器（NEW-003）

    以时间戳队列记录窗口内请求，超限拒绝。
    线程安全；用于网关自身限流并输出规范化 429 头。
    """

    def __init__(self, limit, window_seconds=60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._timestamps = collections.deque()
        self._lock = threading.Lock()

    def _prune(self, now):
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def check(self, now=None):
        """记录一次请求并返回 (allowed, remaining, reset_ts)

        allowed=True 表示放行；reset_ts 为窗口重置时间戳（Unix 秒）。
        """
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            if len(self._timestamps) >= self.limit:
                reset_ts = int(self._timestamps[0]) + self.window_seconds
                return False, 0, reset_ts
            self._timestamps.append(now)
            return True, self.limit - len(self._timestamps), int(now) + self.window_seconds


class APIGateway:
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = BASE_DIR / "config" / "providers.yaml"
        self.config_path = Path(config_path)
        self.usage_path = DATA_DIR / "usage.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_config()
        self._snapshot_config()
        self._setup_logging()
        self._load_usage()
        self.current_provider = None
        self.current_provider_display = None
        self._lock = threading.Lock()
        self._breaker = CircuitBreaker(
            failure_threshold=self.gateway_cfg.get("failure_threshold", 3),
            recovery_timeout=self.retry_seconds,
            failure_rate_threshold=self.gateway_cfg.get("failure_rate_threshold", 0.25),
            min_samples=self.gateway_cfg.get("failure_min_samples", 10),
            window_seconds=self.gateway_cfg.get("failure_window_seconds", 60),
            cooldown_429=self.gateway_cfg.get("cooldown_429", 15),
            cooldown_5xx=self.gateway_cfg.get("cooldown_5xx", 60),
        )
        try:
            self._config_mtime = self.config_path.stat().st_mtime
        except OSError:
            self._config_mtime = 0
        self._config_reload_ts = time.time()
        self._latency_samples = []

    @staticmethod
    def _resolve_env(value):
        """将 ${ENV_VAR} 格式的值替换为环境变量实际值"""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.getenv(env_key, "")
        return value

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请运行 scripts/setup.sh 或复制 config/providers.yaml.example"
            )
        raw = self.config_path.read_text(encoding="utf-8")
        self.config = yaml.safe_load(raw)
        # 环境变量替换：将 ${ENV_VAR} 占位符替换为实际值
        for p in self.config.get("providers", []):
            for field in ("api_key", "access_key_id", "secret_access_key"):
                if field in p:
                    p[field] = self._resolve_env(p[field])
        all_providers = sorted(
            self.config.get("providers", []),
            key=lambda p: p.get("priority", 99)
        )
        # 过滤掉缺少 model 配置的提供商，避免路由时传无效模型名
        self.providers = [p for p in all_providers if p.get("model")]
        missing_model = [p["name"] for p in all_providers if not p.get("model")]
        if missing_model:
            logger.warning(f"以下提供商缺少 model 配置，已跳过: {missing_model}")
        self.gateway_cfg = self.config.get("gateway", {})
        self.retry_seconds = self.gateway_cfg.get("retry_seconds", 60)
        self.request_timeout = self.gateway_cfg.get("timeout", 30)

    def _snapshot_config(self):
        """配置快照（NEW-004）：启动加载成功后自动备份当前配置，保留最近 10 份

        快照文件：backup/config_v<N>_<name>.yaml
        异常仅记录日志，不中断服务启动。
        """
        try:
            backup_dir = BASE_DIR / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stem = self.config_path.stem
            pattern = re.compile(rf"^config_v(\d+)_{re.escape(stem)}\.yaml$")
            existing = sorted(
                (int(m.group(1)), f) for f in backup_dir.glob(f"config_v*_{stem}.yaml")
                if (m := pattern.match(f.name))
            )
            next_index = (existing[-1][0] + 1) if existing else 1
            target = backup_dir / f"config_v{next_index}_{stem}.yaml"
            shutil.copy2(self.config_path, target)
            logger.info(f"配置快照已保存: {target}")
            while len(existing) >= 10:
                _, oldest = existing.pop(0)
                oldest.unlink()
        except Exception as e:
            logger.warning(f"配置快照失败（不影响服务）: {e}")

    def _maybe_reload(self):
        """配置热加载（CONFIG-002）：mtime 检测 + 5s TTL 缓存

        每次调用前检查 config 文件 mtime：
          - mtime 变化 且 距上次加载 >5s → 重载并自动快照（NEW-004 联动）
          - 重载失败（YAML 损坏）→ 保留旧配置 + 日志告警，服务不中断
        """
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            return
        if mtime == getattr(self, "_config_mtime", True):
            return
        now = time.time()
        if now - getattr(self, "_config_reload_ts", 0) < 5:
            return
        old_names = {p["name"] for p in getattr(self, "providers", [])}
        try:
            self._load_config()
            self._config_mtime = mtime
            self._config_reload_ts = now
            self._snapshot_config()
            new_names = {p["name"] for p in self.providers}
            removed = old_names - new_names
            for name in removed:
                self._breaker.reset(name)
                logger.info(f"配置热加载：移除已删除提供商 [{name}] 的熔断器状态")
            logger.info(f"配置热加载完成，当前 {len(self.providers)} 个提供商")
        except Exception as e:
            logger.warning(f"配置热加载失败（保留旧配置继续服务）: {e}")

    def _setup_logging(self):
        log_rel = self.gateway_cfg.get("log", "")
        if log_rel:
            log_file = BASE_DIR / log_rel
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s"))
            logger.addHandler(fh)

    def _load_usage(self):
        if self.usage_path.exists():
            try:
                self.usage = json.loads(self.usage_path.read_text())
            except Exception:
                self.usage = {}
        else:
            self.usage = {}
        self._ensure_usage()

    def _ensure_usage(self):
        changed = False
        for p in self.providers:
            name = p["name"]
            if name not in self.usage:
                self.usage[name] = {
                    "total_requests": 0,
                    "total_tokens": 0,
                    "errors": 0,
                    "last_used": None,
                }
                changed = True
        if changed:
            self._save_usage()

    def _save_usage(self):
        self.usage_path.write_text(
            json.dumps(self.usage, indent=2, ensure_ascii=False)
        )

    @staticmethod
    def _sign_v4(method, url, headers, body, access_key_id, secret_access_key, region, service):
        """Volcengine V4 签名（兼容 AWS SigV4）。"""
        now = datetime.datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        query = parsed.query

        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        content_sha256 = hashlib.sha256(body_bytes).hexdigest()

        canonical_headers = {
            "content-type": "application/json",
            "host": host,
            "x-content-sha256": content_sha256,
            "x-date": amz_date,
        }
        signed_headers = ";".join(sorted(canonical_headers.keys()))

        canonical_request = (
            f"{method}\n{path}\n{query}\n"
            + "\n".join(f"{k}:{v}" for k, v in sorted(canonical_headers.items())) + "\n"
            f"{signed_headers}\n{content_sha256}"
        )

        credential_scope = f"{date_stamp}/{region}/{service}/request"
        string_to_sign = (
            "HMAC-SHA256\n"
            f"{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        def _sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        sk_bytes = secret_access_key.encode("utf-8")
        k_date = _sign(sk_bytes, date_stamp)
        k_region = _sign(k_date, region)
        k_service = _sign(k_region, service)
        k_signing = _sign(k_service, "request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        auth = (
            f"HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        return {
            "Content-Type": "application/json",
            "X-Date": amz_date,
            "X-Content-Sha256": content_sha256,
            "Authorization": auth,
        }

    def _has_creds(self, p):
        auth_type = p.get("auth_type", "bearer")
        if auth_type == "ak_sk":
            return bool(p.get("access_key_id") and p.get("secret_access_key"))
        return bool(p.get("api_key"))

    def get_available_providers(self):
        self._maybe_reload()
        available = []
        for p in self.providers:
            name = p["name"]
            if not self._has_creds(p):
                continue
            if not self._breaker.is_available(name):
                continue
            if not self._breaker.should_allow(name):
                continue
            available.append(p)
        return available

    def _mark_failed(self, name, error_type="5xx"):
        self._breaker.record_failure(name, error_type=error_type)
        with self._lock:
            u = self.usage.get(name, {})
            u["errors"] = u.get("errors", 0) + 1
            self._save_usage()
        logger.warning(f"[{name}] 已标记失败，{self.retry_seconds}s 后重试")

    def _track(self, name, tokens=0):
        with self._lock:
            u = self.usage.get(name, {})
            u["total_requests"] = u.get("total_requests", 0) + 1
            u["total_tokens"] = u.get("total_tokens", 0) + tokens
            u["last_used"] = datetime.datetime.now().isoformat()
            self._save_usage()

    def _record_latency(self, t0):
        """NEW-002：记录单次调用延迟（monotonic 秒）到内存，供 /metrics 直方图"""
        elapsed = time.monotonic() - t0
        with self._lock:
            samples = getattr(self, "_latency_samples", None)
            if samples is None:
                return
            samples.append(elapsed)
            if len(samples) > 1000:
                del samples[: len(samples) - 1000]

    def _retry_delay(self, attempt):
        """指数退避延迟：1s/2s/4s + jitter(±20%)，attempt 从 1 起"""
        import random
        base = 2 ** (attempt - 1)
        return max(0.1, base + random.uniform(-0.2 * base, 0.2 * base))

    def _retry_wait(self, resp, attempt):
        """重试等待：优先上游 Retry-After 头，否则指数退避+jitter"""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return self._retry_delay(attempt)

    def call_api(self, messages, stream=False, model=None, **kwargs):
        _t0 = time.monotonic()
        providers = self.get_available_providers()
        if not providers:
            return {"error": "所有 API 提供商均不可用，请稍后重试或检查配置"}

        max_retries = self.gateway_cfg.get("retry_attempts", 3)
        last_error = None
        last_status = None
        for provider in providers:
            name = provider["name"]
            auth_type = provider.get("auth_type", "bearer")

            endpoint = provider["endpoint"].rstrip("/")
            api_model = provider.get("model") or "gpt-3.5-turbo"
            url = f"{endpoint}/chat/completions"

            payload = {
                "model": api_model,
                "messages": messages,
                "stream": stream,
                **kwargs,
            }

            body_str = json.dumps(payload, ensure_ascii=False)

            if auth_type == "ak_sk":
                ak = provider.get("access_key_id", "")
                sk = provider.get("secret_access_key", "")
                region = provider.get("region", "cn-beijing")
                service = provider.get("service", "ark")
                headers = self._sign_v4("POST", url, {}, body_str, ak, sk, region, service)
            else:
                api_key = provider.get("api_key", "")
                if not api_key:
                    continue
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                extra = provider.get("headers", {})
                if extra:
                    headers.update(extra)

            for attempt in range(1, max_retries + 1):
                logger.info(f"尝试 [{name}] 模型 [{api_model}] (第 {attempt}/{max_retries} 次)")
                try:
                    resp = requests.post(
                        url,
                        data=body_str,
                        headers=headers,
                        timeout=kwargs.get("timeout", self.request_timeout),
                        stream=stream,
                    )

                    if resp.status_code == 401:
                        logger.warning(f"[{name}] 401 认证错误，不重试")
                        self._mark_failed(name, error_type="401")
                        last_error = f"{name}: 401"
                        last_status = 401
                        break
                    if resp.status_code == 429:
                        if attempt < max_retries:
                            wait = self._retry_wait(resp, attempt)
                            logger.warning(f"[{name}] 429 限流，{wait:.1f}s 后重试")
                            time.sleep(wait)
                            continue
                        logger.warning(f"[{name}] 429 重试耗尽")
                        self._mark_failed(name, error_type="429")
                        last_error = f"{name}: 429"
                        last_status = 429
                        break
                    if resp.status_code >= 500:
                        logger.warning(f"[{name}] {resp.status_code} 服务端错误，不重试")
                        self._mark_failed(name, error_type="5xx")
                        last_error = f"{name}: {resp.status_code}"
                        last_status = resp.status_code
                        break
                    if resp.status_code != 200:
                        logger.warning(f"[{name}] {resp.status_code} 异常")
                        self._mark_failed(name, error_type="5xx")
                        last_error = f"{name}: {resp.status_code}"
                        last_status = resp.status_code
                        break

                    self.current_provider = name
                    self.current_provider_display = provider.get("display", name)
                    self._breaker.record_success(name)
                    self._record_latency(_t0)

                    if stream:
                        tokens = self._estimate_tokens(messages)
                        self._track(name, tokens)
                        return _StreamWrapper(resp)

                    data = resp.json()
                    usage_info = data.get("usage", {})
                    tokens = usage_info.get(
                        "total_tokens", self._estimate_tokens(messages)
                    )
                    self._track(name, tokens)
                    return data

                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    if attempt < max_retries:
                        wait = self._retry_delay(attempt)
                        logger.warning(f"[{name}] 瞬时错误({type(e).__name__})，{wait:.1f}s 后重试")
                        time.sleep(wait)
                        continue
                    logger.warning(f"[{name}] 瞬时错误重试耗尽")
                    self._mark_failed(name, error_type="5xx")
                    last_error = f"{name}: {type(e).__name__}"
                    last_status = 503
                    break
                except Exception as e:
                    logger.warning(f"[{name}] {e}")
                    self._mark_failed(name, error_type="5xx")
                    last_error = f"{name}: {e}"
                    last_status = 500
                    break

        logger.error(f"全部 API 不可用，最后错误: {last_error}")
        result = {"error": f"所有 API 均不可用: {last_error}"}
        if last_status:
            result["status_code"] = last_status
        return result

    def _estimate_tokens(self, messages):
        total = 0
        for msg in messages:
            text = str(msg.get("content", ""))
            total += len(text) * 1.5
        return int(total)

    def get_metrics_text(self):
        """NEW-002：Prometheus 文本格式指标（GET /metrics）

        公开只读（用户已确认）。数据源：usage 持久化 + 内存延迟样本 + 熔断器状态。
        指标：fah_requests_total / fah_tokens_total / fah_errors_total（label=provider）、
              fah_circuit_breaker_state(0/1)、fah_available_providers、
              fah_http_latency_seconds（直方图）。
        """
        lines = [
            "# HELP fah_requests_total 网关累计请求数（按提供商）",
            "# TYPE fah_requests_total counter",
        ]
        with self._lock:
            usage = dict(self.usage)
            latencies = list(self._latency_samples)
        for name, u in sorted(usage.items()):
            name = name.replace('"', '\\"')
            lines.append(f'fah_requests_total{{provider="{name}"}} {u.get("total_requests", 0)}')
        lines.extend([
            "# HELP fah_tokens_total 网关累计 token 用量（按提供商）",
            "# TYPE fah_tokens_total counter",
        ])
        for name, u in sorted(usage.items()):
            name = name.replace('"', '\\"')
            lines.append(f'fah_tokens_total{{provider="{name}"}} {u.get("total_tokens", 0)}')
        lines.extend([
            "# HELP fah_errors_total 网关累计错误数（按提供商）",
            "# TYPE fah_errors_total counter",
        ])
        for name, u in sorted(usage.items()):
            name = name.replace('"', '\\"')
            lines.append(f'fah_errors_total{{provider="{name}"}} {u.get("errors", 0)}')

        lines.extend([
            "# HELP fah_circuit_breaker_state 熔断器状态（0=closed/1=open，1=不可用）",
            "# TYPE fah_circuit_breaker_state gauge",
        ])
        detail = self._breaker.get_detail()
        for name, d in sorted(detail.items()):
            name = name.replace('"', '\\"')
            val = 1 if d["state"] == "open" else 0
            lines.append(f'fah_circuit_breaker_state{{provider="{name}"}} {val}')
        available = self.get_available_providers()
        lines.extend([
            "# HELP fah_available_providers 当前可用提供商数",
            "# TYPE fah_available_providers gauge",
            f"fah_available_providers {len(available)}",
        ])

        lines.extend([
            "# HELP fah_http_latency_seconds 网关调用延迟（秒）",
            "# TYPE fah_http_latency_seconds summary",
        ])
        if latencies:
            avg = sum(latencies) / len(latencies)
            lines.append(f"fah_http_latency_seconds{{quantile=\"0.5\"}} {_percentile(latencies, 0.5):.6f}")
            lines.append(f"fah_http_latency_seconds{{quantile=\"0.95\"}} {_percentile(latencies, 0.95):.6f}")
            lines.append(f"fah_http_latency_seconds{{quantile=\"0.99\"}} {_percentile(latencies, 0.99):.6f}")
            lines.append(f"fah_http_latency_seconds_sum {sum(latencies):.6f}")
            lines.append(f"fah_http_latency_seconds_count {len(latencies)}")
        return "\n".join(lines) + "\n"

    def get_status(self):
        self._maybe_reload()
        available = self.get_available_providers()
        breaker_detail = self._breaker.get_detail()
        return {
            "current_provider": self.current_provider,
            "available_providers": [p["name"] for p in available],
            "failed_providers": self._breaker.get_failed_names(),
            "circuit_breakers": breaker_detail,
            "providers_configured": [
                {
                    "name": p["name"],
                    "display": p.get("display", p["name"]),
                    "model": p.get("model", ""),
                    "priority": p.get("priority", 99),
                    "has_key": self._has_creds(p),
                    "auth_type": p.get("auth_type", "bearer"),
                }
                for p in self.providers
            ],
            "usage": self.usage,
        }

    def is_ready(self):
        """Readiness 检查：至少 1 个 provider 可用即就绪"""
        available = self.get_available_providers()
        return len(available) > 0

    def health_detail(self):
        """返回详细健康信息供 readiness 探针使用"""
        available = self.get_available_providers()
        failed_names = self._breaker.get_failed_names()
        total = len(self.providers)
        ready_count = len(available)
        return {
            "ready": ready_count > 0,
            "total_providers": total,
            "available_providers": ready_count,
            "failed_providers": len(failed_names),
            "available_names": [p["name"] for p in available],
            "failed_names": failed_names,
            "circuit_breakers": self._breaker.get_detail(),
        }

    def list_models(self):
        self._maybe_reload()
        models = []
        for p in self.providers:
            if not self._has_creds(p):
                continue
            models.append({
                "id": p.get("model", "unknown"),
                "provider": p["name"],
                "display": p.get("display", p["name"]),
                "priority": p.get("priority", 99),
            })
        return {"object": "list", "data": models}

    def reset_failures(self):
        self._breaker.reset()
        logger.info("已重置所有提供商熔断器状态")


class _StreamWrapper:
    """包装流式响应，逐行产出 SSE data 内容"""
    def __init__(self, response):
        response.encoding = 'utf-8'
        self._iterator = response.iter_lines(decode_unicode=True)

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            line = next(self._iterator)
            if not line:
                continue
            if line.startswith("data: "):
                chunk = line[6:]
                if chunk.strip() == "[DONE]":
                    raise StopIteration
                return chunk
            return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gw = APIGateway()
    print(json.dumps(gw.get_status(), indent=2, ensure_ascii=False))
