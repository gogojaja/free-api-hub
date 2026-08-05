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
import urllib.parse
import requests
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class APIGateway:
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = BASE_DIR / "config" / "providers.yaml"
        self.config_path = Path(config_path)
        self.usage_path = DATA_DIR / "usage.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_config()
        self._setup_logging()
        self._load_usage()
        self.current_provider = None
        self.current_provider_display = None
        self.failed_providers = {}

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {self.config_path}\n"
                f"请运行 scripts/setup.sh 或复制 config/providers.yaml.example"
            )
        raw = self.config_path.read_text(encoding="utf-8")
        self.config = yaml.safe_load(raw)
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
        now = time.time()
        available = []
        for p in self.providers:
            name = p["name"]
            if not self._has_creds(p):
                continue
            if name in self.failed_providers:
                failed_at = self.failed_providers[name]
                if now - failed_at < self.retry_seconds:
                    continue
                del self.failed_providers[name]
            available.append(p)
        return available

    def _mark_failed(self, name):
        self.failed_providers[name] = time.time()
        u = self.usage.get(name, {})
        u["errors"] = u.get("errors", 0) + 1
        self._save_usage()
        logger.warning(f"[{name}] 已标记失败，{self.retry_seconds}s 后重试")

    def _track(self, name, tokens=0):
        u = self.usage.get(name, {})
        u["total_requests"] = u.get("total_requests", 0) + 1
        u["total_tokens"] = u.get("total_tokens", 0) + tokens
        u["last_used"] = datetime.datetime.now().isoformat()
        self._save_usage()

    def call_api(self, messages, stream=False, model=None, **kwargs):
        providers = self.get_available_providers()
        if not providers:
            return {"error": "所有 API 提供商均不可用，请稍后重试或检查配置"}

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

            logger.info(f"尝试 [{name}] 模型 [{api_model}]")
            try:
                resp = requests.post(
                    url,
                    data=body_str,
                    headers=headers,
                    timeout=kwargs.get("timeout", self.request_timeout),
                    stream=stream,
                )

                if resp.status_code in (429, 401):
                    logger.warning(f"[{name}] {resp.status_code}，切换下一家")
                    self._mark_failed(name)
                    last_error = f"{name}: {resp.status_code}"
                    continue
                if resp.status_code >= 500:
                    logger.warning(f"[{name}] {resp.status_code} 服务端错误")
                    self._mark_failed(name)
                    last_error = f"{name}: {resp.status_code}"
                    continue
                if resp.status_code != 200:
                    logger.warning(f"[{name}] {resp.status_code} 异常")
                    self._mark_failed(name)
                    last_error = f"{name}: {resp.status_code}"
                    continue

                self.current_provider = name
                self.current_provider_display = provider.get("display", name)
                self.failed_providers.pop(name, None)

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

            except requests.exceptions.Timeout:
                logger.warning(f"[{name}] 超时")
                self._mark_failed(name)
                last_error = f"{name}: Timeout"
            except requests.exceptions.ConnectionError:
                logger.warning(f"[{name}] 连接失败")
                self._mark_failed(name)
                last_error = f"{name}: ConnectionError"
            except Exception as e:
                logger.warning(f"[{name}] {e}")
                self._mark_failed(name)
                last_error = f"{name}: {e}"

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
        return {
            "current_provider": self.current_provider,
            "available_providers": [p["name"] for p in available],
            "failed_providers": list(self.failed_providers.keys()),
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
        self.failed_providers = {}
        logger.info("已重置所有提供商失败状态")


class _StreamWrapper:
    """包装流式响应，逐行产出 SSE data 内容"""
    def __init__(self, response):
        response.encoding = 'utf-8'
        self._iterator = response.iter_lines(decode_unicode=True)

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self._iterator)
        if not line:
            return self.__next__()
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
