"""
Free API Hub — Flask HTTP 服务
暴露 OpenAI 兼容接口，自动 failover 多提供商
"""
import os
import sys
import json
import logging
import urllib.parse
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, request, jsonify, Response, stream_with_context
from gateway import APIGateway, _load_dotenv

_load_dotenv()

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

gateway = None

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def require_auth(f):
    """管理端点认证装饰器：校验 Bearer Token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_TOKEN:
            logger.warning("ADMIN_TOKEN 未配置，管理端点无认证保护")
            return f(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {ADMIN_TOKEN}":
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    if gateway is None:
        return jsonify({"error": "网关未初始化，请先运行 scripts/setup.sh"}), 503

    try:
        body = request.get_json(force=True)
        if not body or "messages" not in body:
            return jsonify({"error": "Missing 'messages' in request body"}), 400

        messages = body["messages"]
        stream = body.get("stream", False)
        model = body.get("model")
        kwargs = {k: v for k, v in body.items()
                  if k not in ("messages", "stream", "model")}

        if stream:
            return _handle_stream(messages, model, **kwargs)

        result = gateway.call_api(messages, stream=False, model=model, **kwargs)
        if isinstance(result, dict) and "error" in result:
            return jsonify(result), 503
        if isinstance(result, dict):
            result["provider_name"] = gateway.current_provider
            result["provider_display"] = gateway.current_provider_display
        return jsonify(result)

    except Exception as e:
        logger.error(f"处理请求异常: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _handle_stream(messages, model=None, **kwargs):
    result = gateway.call_api(messages, stream=True, model=model, **kwargs)
    provider_name = gateway.current_provider
    provider_display = gateway.current_provider_display

    def generate():
        if isinstance(result, dict) and "error" in result:
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            for chunk in result:
                if chunk:
                    yield f"data: {chunk}\n\n"
        except StopIteration:
            pass
        except Exception as e:
            logger.error(f"流式输出异常: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if provider_name:
        headers["X-Provider-Name"] = provider_name
        headers["X-Provider-Display"] = urllib.parse.quote(provider_display or provider_name)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers=headers,
    )


@app.route("/v1/models", methods=["GET"])
def list_models():
    if gateway is None:
        return jsonify({"error": "网关未初始化"}), 503
    try:
        return jsonify(gateway.list_models())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gateway/status", methods=["GET"])
@require_auth
def gateway_status():
    if gateway is None:
        return jsonify({"error": "网关未初始化"}), 503
    try:
        return jsonify(gateway.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gateway/reset", methods=["POST"])
@require_auth
def reset_gateway():
    if gateway is None:
        return jsonify({"error": "网关未初始化"}), 503
    try:
        gateway.reset_failures()
        return jsonify({"status": "ok", "message": "失败状态已重置"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health/live", methods=["GET"])
def health_live():
    """Liveness 探针：进程存活即返回 200"""
    return jsonify({"status": "alive"}), 200


@app.route("/health/ready", methods=["GET"])
def health_ready():
    """Readiness 探针：网关初始化且至少 1 个 provider 可用才返回 200"""
    if gateway is None:
        return jsonify({"status": "not_ready", "reason": "gateway_not_initialized"}), 503
    detail = gateway.health_detail()
    if detail["ready"]:
        return jsonify({"status": "ready", **detail}), 200
    return jsonify({"status": "not_ready", **detail}), 503


@app.route("/health", methods=["GET"])
def health():
    """向后兼容：等同 /health/live"""
    return jsonify({"status": "ok", "gateway_ready": gateway is not None})


def run_server(config_path=None):
    global gateway
    try:
        gateway = APIGateway(config_path=config_path)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    port = gateway.gateway_cfg.get("port", 5080)
    logger.info(f"Free API Hub 启动在 http://127.0.0.1:{port}")
    logger.info(f"  配置: {config_path or 'config/providers.yaml'}")
    logger.info(f"  POST /v1/chat/completions — OpenAI 兼容接口")
    logger.info(f"  GET  /v1/models          — 可用模型列表")
    logger.info(f"  GET  /gateway/status      — 网关状态")
    logger.info(f"  POST /gateway/reset       — 重置失败状态")
    logger.info(f"  GET  /health/live         — Liveness 探针")
    logger.info(f"  GET  /health/ready        — Readiness 探针")
    logger.info(f"  GET  /health              — 健康检查（向后兼容）")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Free API Hub")
    parser.add_argument("--config", default=None,
                        help="配置文件路径（默认 config/providers.yaml）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(asctime)s - %(message)s",
    )
    run_server(config_path=args.config)
