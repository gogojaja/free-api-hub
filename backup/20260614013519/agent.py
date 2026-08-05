"""
AI Agent 层 — 智能编程助手
接收 opencode 请求 → 注入 System Prompt → 工具执行 → 反馈闭环
通过 Free API Hub 网关 (:5081) 调用下游模型，利用其自动 failover

对外接口:
    - POST /v1/chat/completions: 完成对话（流式/非流式兼容）
    - GET /health: 健康检查

依赖:
    - 标准库: os, sys, json, logging, subprocess, tempfile, re, time, threading
    - 第三方: flask, requests
    - 项目内: 无（独立运行，通过 HTTP 调用 Free API Hub）

版本: v1.0
"""
import os
import sys
import json
import logging
import subprocess
import tempfile
import re
import time
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
from flask import Flask, request, jsonify, Response, stream_with_context

# ============================================================================
# 日志
# ============================================================================
logger = logging.getLogger("Agent")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(ch)

    log_dir = Path(__file__).resolve().parent.parent / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "[%(levelname)s] %(asctime)s - %(name)s - %(message)s"
    ))
    logger.addHandler(fh)

# ============================================================================
# 配置
# ============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent
FREE_API_HUB_CODE = os.getenv("FREE_API_HUB_CODE", "http://127.0.0.1:5081/v1")
MAX_LOOP_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))
AGENT_PORT = int(os.getenv("AGENT_PORT", "5090"))
MAX_TOOL_OUTPUT = 10000

# ============================================================================
# 工程化 System Prompt
# ============================================================================
SYSTEM_PROMPT = """# 编程助手

你是一个编程助手，回复用中文，代码用英文。

你可以输出 ```python 代码块，我会执行并返回结果给你。
你可以多次输出代码块完成多步操作（比如先读文件，再修改文件）。

## 工作方式
- 需要查文件时：输出 print(open('path').read())
- 需要修改文件时：输出 open('path', 'w').write('新内容') 或 Python 文件操作
- 需要新代码时：输出代码直接运行
- 任务完成后：输出一段总结，不要再带代码块"""

NUDGE_PROMPT = """用 ```python 代码块 操作文件或运行代码。任务完成后输出总结。"""


# ============================================================================
# 工具执行器
# ============================================================================
class ToolExecutor:
    def __init__(self, work_dir: Optional[Path] = None):
        self.work_dir = work_dir or BASE_DIR

    def execute(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"_{tool_name}", None)
        if not handler:
            return {"status": "error", "error": f"未知工具: {tool_name}"}
        try:
            result = handler(**params)
            return {"status": "success", "content": result[:MAX_TOOL_OUTPUT]}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _execute_code(self, code: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp = f.name
        try:
            r = subprocess.run(
                [sys.executable, tmp],
                capture_output=True, text=True, timeout=30,
                cwd=str(self.work_dir)
            )
            out = []
            if r.stdout:
                out.append(f"[stdout]\n{r.stdout}")
            if r.stderr:
                out.append(f"[stderr]\n{r.stderr}")
            out.append(f"[exit_code] {r.returncode}")
            return "\n".join(out)
        except subprocess.TimeoutExpired:
            return "[error] 执行超时 (30s)"
        finally:
            os.unlink(tmp)

    def _read_file(self, file_path: str) -> str:
        p = self.work_dir / file_path
        if not p.exists():
            p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        content = p.read_text(encoding="utf-8")
        return f"=== {p} ===\n{content}"

    def _write_file(self, file_path: str, content: str) -> str:
        p = self.work_dir / file_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {p} ({len(content)} 字符)"

    def _run_command(self, command: str, cwd: Optional[str] = None) -> str:
        wd = str(self.work_dir / cwd) if cwd else str(self.work_dir)
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=60, cwd=wd
        )
        out = []
        if r.stdout:
            out.append(f"[stdout]\n{r.stdout}")
        if r.stderr:
            out.append(f"[stderr]\n{r.stderr}")
        out.append(f"[exit_code] {r.returncode}")
        return "\n".join(out)


# ============================================================================
# Agent 引擎
# ============================================================================
class AgentEngine:
    def __init__(self):
        self.tool_executor = ToolExecutor()
        logger.info("Agent 引擎初始化完成")

    def parse_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        calls = []
        pattern = r"<tool_call>\s*<name>(.*?)</name>\s*<parameters>(.*?)</parameters>\s*</tool_call>"
        for m in re.finditer(pattern, text, re.DOTALL):
            name = m.group(1).strip()
            try:
                params = json.loads(m.group(2).strip())
            except json.JSONDecodeError:
                params = {}
            calls.append({"name": name, "parameters": params})
        return calls

    def strip_tool_calls(self, text: str) -> str:
        return re.sub(
            r"<tool_call>.*?</tool_call>",
            "",
            text,
            flags=re.DOTALL
        ).strip()

    def process(self, messages: List[Dict]) -> Dict[str, Any]:
        has_system = any(m.get("role") == "system" for m in messages)
        full_messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}] + messages
            if not has_system else messages
        )

        parts_content = ""  # 累积用户可见的部分

        for iteration in range(MAX_LOOP_ITERATIONS):
            logger.info(f"--- Agent 迭代 {iteration + 1}/{MAX_LOOP_ITERATIONS} ---")

            response = self._call_llm(full_messages)
            if "error" in response:
                return response

            content = response.get("content", "")
            tool_calls = self.parse_tool_calls(content) or response.get("tool_calls", [])
            code_from_block = self._extract_code_block(content)
            if not code_from_block:
                code_from_block = self._try_extract_bare_code(content)

            if tool_calls:
                logger.info(f"检测到 {len(tool_calls)} 个工具调用")
                clean = self.strip_tool_calls(content)
                full_messages.append({"role": "assistant", "content": clean or None})
                for tc in tool_calls:
                    r = self.tool_executor.execute(tc["name"], tc["parameters"])
                    logger.info(f"工具结果: {r['status']}")
                    full_messages.append({
                        "role": "tool", "tool_call_id": tc["name"],
                        "content": r.get("content") or r.get("error", "")
                    })
                continue

            if code_from_block:
                logger.info("检测到 Python 代码块，自动执行")
                r = self.tool_executor.execute("execute_code", {"code": code_from_block})
                logger.info(f"自动执行结果: {r['status']}")
                clean = self._strip_after_code_block(content)
                exec_result = r.get("content", "")
                parts_content += f"{clean}\n{exec_result}\n\n"
                full_messages.append({"role": "assistant", "content": content})
                full_messages.append({
                    "role": "user",
                    "content": f"代码执行结果：\n{exec_result}"
                })
                continue

            # 第一轮无代码块 → 强制要求后重试
            if iteration == 0:
                logger.info("模型未输出代码块，强制要求编写代码")
                full_messages.append({"role": "assistant", "content": content})
                full_messages.append({
                    "role": "user",
                    "content": NUDGE_PROMPT
                })
                continue

            logger.info("无工具调用和代码块，返回结果")
            final = parts_content + content if parts_content else content
            return {"role": "assistant", "content": final.strip(), "iterations": iteration + 1}

        logger.warning(f"达到最大迭代次数 {MAX_LOOP_ITERATIONS}")
        return {"role": "assistant", "content": "处理超过最大迭代次数，请简化需求或重试。", "iterations": MAX_LOOP_ITERATIONS}

    def _extract_code_block(self, text: str) -> Optional[str]:
        """提取 ```python ... ``` 代码块内容"""
        m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            code = m.group(1).strip()
            if code and len(code) > 10:
                return code
        return None

    def _try_extract_bare_code(self, text: str) -> Optional[str]:
        """检测无 ``` 标记的裸露 Python 代码"""
        stripped = text.strip()
        python_keywords = {'def ', 'class ', 'import ', 'from ', 'print(',
                           'if __name__', '#', '@', 'async def'}
        lines = stripped.split('\n')
        code_lines = []
        in_code = False
        for line in lines:
            sl = line.strip()
            if not in_code:
                if any(sl.startswith(kw) for kw in python_keywords):
                    in_code = True
                    code_lines.append(line)
            elif sl == '' or any(sl.startswith(kw) for kw in python_keywords):
                code_lines.append(line)
            else:
                break
        code = '\n'.join(code_lines).strip()
        if code and len(code) > 15:
            return code
        return None

    def _strip_after_code_block(self, text: str) -> str:
        """只保留代码块之前和代码块本身，去掉代码块之后的文字"""
        m = re.search(r"```(?:python)?\s*\n.*?```", text, re.DOTALL)
        if m:
            return text[:m.end()].strip()
        return text

    def _call_llm(self, messages: List[Dict], retry: bool = True) -> Dict[str, Any]:
        payload = {
            "model": "agent-router",
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 8192,
            "stream": False
        }
        try:
            logger.debug(f"调用 Free API Hub (code 实例)...")
            resp = requests.post(
                f"{FREE_API_HUB_CODE}/chat/completions",
                json=payload,
                timeout=300
            )
            if resp.status_code != 200:
                if retry:
                    logger.warning(f"上游返回 {resp.status_code}，等待 3s 后重试...")
                    time.sleep(3)
                    return self._call_llm(messages, retry=False)
                return {"error": f"上游全部不可用，请稍后重试"}

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            result = {"content": msg.get("content", "")}

            # OpenAI function calling
            oai_tool_calls = msg.get("tool_calls")
            if oai_tool_calls:
                parsed = []
                for tc in oai_tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    try:
                        params = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        params = {}
                    parsed.append({"name": name, "parameters": params})
                result["tool_calls"] = parsed
                result["content"] = re.sub(
                    r"<tool_call>.*?</tool_call>",
                    "",
                    result["content"],
                    flags=re.DOTALL
                ).strip()

            return result

        except requests.exceptions.Timeout:
            if retry:
                logger.warning("上游超时，等待 3s 后重试...")
                time.sleep(3)
                return self._call_llm(messages, retry=False)
            return {"error": "上游请求超时（超过 300 秒），请重试或简化需求"}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:100]}"}


# ============================================================================
# Flask 服务器
# ============================================================================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
agent_engine = AgentEngine()


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    try:
        body = request.get_json(force=True)
        if not body or "messages" not in body:
            return jsonify({"error": "Missing 'messages'"}), 400

        messages = body["messages"]
        stream = body.get("stream", False)

        if stream:
            def generate():
                start = time.time()
                # Send initial chunk ASAP so opencode doesn't timeout
                initial = {
                    "id": f"chatcmpl-{int(time.time() * 1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "agent-code-v1",
                    "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"

                # Now process (blocking but connection is already live)
                result = agent_engine.process(messages)
                elapsed = time.time() - start

                if "error" in result:
                    err_text = result["error"]
                    if "超时" in err_text:
                        friendly = f"请求超时了，目前上游 API 响应较慢（可能需要 30-60 秒）。请重试，如果持续超时可以换个简单点的需求。"
                    elif "全部不可用" in err_text:
                        friendly = f"所有上游 API 暂时不可用（限流或超时），请等待 10 秒后重试。"
                    else:
                        friendly = f"处理出错了：{err_text}。请重试。"
                    err_chunk = {
                        "id": f"chatcmpl-{int(time.time() * 1000)}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "agent-code-v1",
                        "choices": [{"index": 0, "delta": {"content": friendly}, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                content = result.get("content", "")
                iters = result.get("iterations", 0)
                logger.info(f"完成: {len(content)} 字符, {iters} 次迭代, {elapsed:.1f}s")

                # Stream content in chunks
                content_so_far = ""
                def flush():
                    nonlocal content_so_far
                    if not content_so_far:
                        return
                    chunk = {
                        "id": f"chatcmpl-{int(time.time() * 1000)}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "agent-code-v1",
                        "choices": [{
                            "index": 0,
                            "delta": {"content": content_so_far},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    content_so_far = ""
                for char in content:
                    content_so_far += char
                    if content_so_far.endswith("\n") or len(content_so_far) >= 50:
                        yield from flush()
                yield from flush()

                final_chunk = {
                    "id": f"chatcmpl-{int(time.time() * 1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "agent-code-v1",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                }
                yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return Response(stream_with_context(generate()), mimetype="text/event-stream")
        else:
            start = time.time()
            result = agent_engine.process(messages)
            elapsed = time.time() - start

            if "error" in result:
                logger.error(f"处理失败: {result['error']}")
                err = result["error"]
                if "超时" in err:
                    return jsonify({"error": "请求超时，上游 API 响应较慢，请重试"}), 503
                if "全部不可用" in err:
                    return jsonify({"error": "所有上游 API 暂时不可用，请等待 10 秒后重试"}), 503
                return jsonify({"error": err}), 503

            content = result.get("content", "")
            iters = result.get("iterations", 0)
            logger.info(f"完成: {len(content)} 字符, {iters} 次迭代, {elapsed:.1f}s")

            return jsonify({
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "agent-code-v1",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            })

    except Exception as e:
        logger.error(f"请求异常: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent_ready": True})


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({
        "object": "list",
        "data": [{"id": "agent-code-v1", "object": "model"}]
    })


# ============================================================================
# 入口
# ============================================================================
def run_server():
    logger.info(f"=" * 50)
    logger.info(f"AI Agent 层启动")
    logger.info(f"  端口: {AGENT_PORT}")
    logger.info(f"  上游: {FREE_API_HUB_CODE}")
    logger.info(f"  最大迭代: {MAX_LOOP_ITERATIONS}")
    logger.info(f"=" * 50)
    app.run(host="127.0.0.1", port=AGENT_PORT, debug=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Agent Layer")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s"
    )

    AGENT_PORT = args.port
    run_server()
