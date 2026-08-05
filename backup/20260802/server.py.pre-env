"""
Free API Hub — Flask HTTP 服务
暴露 OpenAI 兼容接口，自动 failover 多提供商
"""
import os
import sys
import json
import logging
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, request, jsonify, Response, stream_with_context
from gateway import APIGateway

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

gateway = None


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
def gateway_status():
    if gateway is None:
        return jsonify({"error": "网关未初始化"}), 503
    try:
        return jsonify(gateway.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gateway/reset", methods=["POST"])
def reset_gateway():
    if gateway is None:
        return jsonify({"error": "网关未初始化"}), 503
    try:
        gateway.reset_failures()
        return jsonify({"status": "ok", "message": "失败状态已重置"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
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
    logger.info(f"  GET  /health              — 健康检查")
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
