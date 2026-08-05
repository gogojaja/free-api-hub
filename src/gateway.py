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


class CircuitBreaker:
    """熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED

    状态流转：
      CLOSED    — 正常，记录失败次数；达到 failure_threshold 后 → OPEN
      OPEN      — 熔断，拒绝请求；经过 recovery_timeout 后 → HALF_OPEN
      HALF_OPEN — 探测，放行请求；成功 → CLOSED，失败 → OPEN
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold=3, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._states = {}  # name -> {state, failure_count, last_failure_ts}
        self._lock = threading.Lock()

    def _get_state(self, name):
        """获取当前状态（含 OPEN→HALF_OPEN 自动转换）"""
        info = self._states.get(name)
        if not info:
            return self.CLOSED
        if info["state"] == self.OPEN:
            if time.time() - info["last_failure_ts"] >= self.recovery_timeout:
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

    def record_success(self, name):
        """记录成功：重置为 CLOSED"""
        with self._lock:
            self._states.pop(name, None)

    def record_failure(self, name):
        """记录失败：累加计数，可能触发熔断"""
        with self._lock:
            info = self._states.get(name, {
                "state": self.CLOSED,
                "failure_count": 0,
                "last_failure_ts": 0,
            })
            info["failure_count"] += 1
            info["last_failure_ts"] = time.time()
            if info["state"] == self.HALF_OPEN:
                info["state"] = self.OPEN
                info["failure_count"] = 0
            elif info["failure_count"] >= self.failure_threshold:
                info["state"] = self.OPEN
            self._states[name] = info

    def reset(self, name=None):
        """重置熔断器"""
        with self._lock:
            if name:
                self._states.pop(name, None)
            else:
                self._states.clear()

    def get_detail(self):
        """获取所有 provider 的熔断状态"""
        with self._lock:
            result = {}
            for name, info in self._states.items():
                state = self._get_state(name)
                result[name] = {
                    "state": state,
                    "failure_count": info["failure_count"],
                }
            return result

    def get_failed_names(self):
        """获取处于 OPEN 状态的 provider 名称列表"""
        with self._lock:
            return [name for name, info in self._states.items()
                    if self._get_state(name) == self.OPEN]


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
        )

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
        available = []
        for p in self.providers:
            name = p["name"]
            if not self._has_creds(p):
                continue
            if not self._breaker.is_available(name):
                continue
            available.append(p)
        return available

    def _mark_failed(self, name):
        self._breaker.record_failure(name)
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
        providers = self.get_available_providers()
        if not providers:
            return {"error": "所有 API 提供商均不可用，请稍后重试或检查配置"}

        max_retries = self.gateway_cfg.get("retry_attempts", 3)
        last_error = None
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
                        self._mark_failed(name)
                        last_error = f"{name}: 401"
                        break
                    if resp.status_code == 429:
                        if attempt < max_retries:
                            wait = self._retry_wait(resp, attempt)
                            logger.warning(f"[{name}] 429 限流，{wait:.1f}s 后重试")
                            time.sleep(wait)
                            continue
                        logger.warning(f"[{name}] 429 重试耗尽")
                        self._mark_failed(name)
                        last_error = f"{name}: 429"
                        break
                    if resp.status_code >= 500:
                        logger.warning(f"[{name}] {resp.status_code} 服务端错误，不重试")
                        self._mark_failed(name)
                        last_error = f"{name}: {resp.status_code}"
                        break
                    if resp.status_code != 200:
                        logger.warning(f"[{name}] {resp.status_code} 异常")
                        self._mark_failed(name)
                        last_error = f"{name}: {resp.status_code}"
                        break

                    self.current_provider = name
                    self.current_provider_display = provider.get("display", name)
                    self._breaker.record_success(name)

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
                    self._mark_failed(name)
                    last_error = f"{name}: {type(e).__name__}"
                    break
                except Exception as e:
                    logger.warning(f"[{name}] {e}")
                    self._mark_failed(name)
                    last_error = f"{name}: {e}"
                    break

        logger.error(f"全部 API 不可用，最后错误: {last_error}")
        return {"error": f"所有 API 均不可用: {last_error}"}

    def _estimate_tokens(self, messages):
        total = 0
        for msg in messages:
            text = str(msg.get("content", ""))
            total += len(text) * 1.5
        return int(total)

    def get_status(self):
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
