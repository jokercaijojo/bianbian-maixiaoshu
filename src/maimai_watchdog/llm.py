"""LLM 闲聊层：智谱 GLM（OpenAI 兼容接口）。

安全与成本控制：
- API Key 只从环境变量读取，服务端代理调用，用户不可见
- 每用户每日配额（默认 20 次），超限礼貌拒绝
- max_tokens 硬封顶，系统提示词限定话题范围，重型任务（写代码等）拒答
"""
from __future__ import annotations

import json
import os
import urllib.request

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = os.environ.get("ZHIPU_MODEL", "glm-4.6")

SYSTEM_PROMPT = (
    "你是「麦麦管家」，一个住在用户微信里的麦当劳优惠小助手，语气亲切、简短、"
    "带点俏皮，多用口语，回答控制在100字以内。\n"
    "你可以聊的话题：麦当劳（优惠、餐品、点餐建议）、日常闲聊、天气、生活小事。\n"
    "你必须拒绝的请求：写代码、写论文、翻译长文、生成大段内容、任何明显消耗"
    "大量 token 的复杂任务——礼貌拒绝并引导回优惠话题，例如："
    "「这个太硬核啦，我只是个盯优惠的小管家🍔 要不我帮你看看今天有什么券？」"
)

MAX_OUTPUT_TOKENS = 300


class LLMError(RuntimeError):
    pass


def chat(user_text: str, history: list[dict] | None = None) -> str:
    key = os.environ.get("ZHIPU_API_KEY", "")
    if not key:
        raise LLMError("未配置 ZHIPU_API_KEY")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend((history or [])[-6:])  # 只带最近几轮，省 token
    messages.append({"role": "user", "content": user_text})
    body = json.dumps({
        "model": DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.7,
        "thinking": {"type": "disabled"},  # 闲聊场景关闭深度思考，省 token、降延迟
    }).encode()
    req = urllib.request.Request(ZHIPU_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"].strip()
