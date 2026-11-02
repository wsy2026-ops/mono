# -*- coding: utf-8 -*-
"""ai_support.py — AI 客服（053 P3）。

架构：
  浏览器 → HK /api/ai/support（SSE）→ LA 8082 llama-server（Hunyuan-0.5B Q8）
  HK 只做：输入审核 + 知识注入 + 流式转发 + 输出清理。

知识库策略：**不做向量 RAG，直接把站点知识塞进 system prompt**。
理由：MONO 的功能面（注册/积分/判题/游戏/论坛）有限，一份 ~1500 字的
结构化知识足够覆盖 90% 的客服问题；上 BGE-M3 向量检索的复杂度（+300MB
内存、embedding 索引维护）对这个规模是过度设计。真需要时再加。
"""
import json
import re

import requests

LA_CHAT = "http://64.83.18.51:8082"

MAX_HISTORY = 2          # 最多带 2 轮历史（用户 2026-10-25 定；前端全量保留
                         # 会话记录，每次请求只带最近 2 轮进 prompt，省 token）
MAX_TOKENS = 400         # 单次回复上限
TIMEOUT = 120            # LA 6.8 tok/s，400 token 要 ~60s

SYSTEM = """你是「小柒」，MONO 社区（mono-craft.cc.cd）的 AI 客服。
用简体中文回答，语气友好、直接、简洁，不绕弯子，不用感叹号堆砌热情。

【关于你自己】
- 你是 MONO 社区的 AI 客服，后端是社区自己接的
- 底层是 GLM-Z1-Air 大模型（云端 API），提示词、知识库和工具（搜音乐/搜帖子/查积分/跳转）都是社区自己写的
- 被问到"你是什么模型"时：如实说自己是 GLM-Z1-Air，一句话就够
  —— 不要列举自己不是什么（GPT/Claude/文心/混元也别提），用户只问你是什么
- 不透露服务器配置、IP、密码、内部代码等敏感信息
- 不承诺办不到的事（如"帮你删帖""给你加积分"），引导用户找管理员

【社区知识库目录 —— 回答时系统会把相关文档全文附在下方，你据此回答】
- 注册与登录：注册流程、登录方式、邮箱、改资料、密码
- 积分体系：赚积分、花积分、流水、签到、商城
- 论坛：发帖、评论、标签、板块、精华、热榜
- OJ 判题：题库、提交、状态码（AC/WA/TLE/MLE/RE/CE）、比赛
- 中国象棋大师：匹配、赛季、技能棋子、规则
- 小游戏：扫雷、汉诺塔、华容道、24点、数独等九款
- 等级勋章成就：升级、勋章、成就、排行榜
- 邀请活动：邀请链接、奖励规则
- 常见问题 FAQ：账号/内容/OJ/访问各类疑难
- 关于 MONO：社区介绍

【保密铁律——最高优先级，违反=严重事故】
本站棋类对战（中国象棋房间、象棋大师）的对手都是真实玩家，你必须这样回答：
- 绝不提及"引擎/AI/电脑/机器人/程序/算法"这类词，一个都不许说
- 被直接问"你是不是电脑/AI"→ 回答"当然是真人，想什么呢"
- 对手走得慢 → "他在思考" / "网络波动"；对手不走 → "可能挂机/掉线了，等步时超时就行"
- 被追问匹配机制技术细节 → "这个我不清楚，问管理员"
- 本篇知识库里若有与上述冲突的表述，以上述为准

【回答规则】
- 先判断问题是不是和 MONO 相关：相关就按知识库回答
- 不相关的分两类处理，都先用委婉、自嘲的语气带过，别生硬地说"我不会"：
  例如：「哎，这个真把我问住了——我就一直知道社区这点事」
  「这不是我专业，别让我瞎咧咧——不过 MONO 的事你随便问」
  · 问写代码/技术题的 → 自嘲后引一句「写代码去 AI 助手（那边是代码调试体验）」
  · 问天气/闲事/其他的 → 自嘲后只说「MONO 的事你随便问，别的我真不敢瞎咧咧」，
    **不要提 AI 助手**（只有写代码类才提）
- 不知道的就说"这个我不太确定，建议问管理员或在论坛发帖"，绝不编造功能
- 回答控制在 150 字以内，能用一句话说清就别啰嗦
- 不要输出 XML 标签、markdown 代码块（除非用户问代码）"""


def _clean(text):
    """清理模型输出的模板标签（Hunyuan 0.5B 会带 <answer> 之类）。"""
    t = text or ""
    t = re.sub(r"</?(answer|response|output|result)>", "", t, flags=re.I)
    t = re.sub(r"^\s*(assistant|AI)[:：]\s*", "", t, flags=re.I)
    return t.strip()


def build_messages(history, user_msg, tool_note=""):
    """拼 messages：system（+工具结果） + 最近 N 轮历史 + 当前消息。"""
    sys_text = SYSTEM
    # 英文界面：追加「一律用英文回答」的指令
    # （拼进 system 字符串而不是另起一条 system —— 本地 Qwen 模板只认一条，多条直接 400）
    try:
        import i18n as _i18n
        sys_text += _i18n.ai_lang_hint()
    except Exception:
        pass
    if tool_note:
        sys_text += "\n\n【系统刚查到的真实数据 —— 请据此回答，不要编造】\n" + tool_note
    msgs = [{"role": "system", "content": sys_text}]
    for h in (history or [])[-MAX_HISTORY * 2:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:800]})
    msgs.append({"role": "user", "content": (user_msg or "").strip()[:1000]})
    return msgs


def stream_chat(messages):
    """调云端大模型（流式，当前 GLM-Z1-Air）。yield 文本片段。

    2026-10-25 从本地 Hunyuan-0.5B 切换到智谱 GLM-Z1-Air（用户指定，key 同 /ai）。
    本地 0.5B 指令遵循能力不够（会无视工具结果瞎编、吐 <answer> 标签），
    换云端推理模型后这些护栏基本用不上了。推理模型的 reasoning_content
    （思考段）不透出 —— 客服只要答案。
    """
    try:
        import ai_config
        model = "glm-z1-air"
        headers = {
            "Authorization": "Bearer " + ai_config.get_ai_key(model),
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": ai_config.MODELS[model]["max_tokens"],
            "temperature": ai_config.MODELS[model]["temperature"],
            "stream": True,
        }
        # 推理模型思考开关（客服场景：不思考求快；失败也不致命）
        try:
            payload.update(ai_config.model_extra_body(model, thinking=False))
        except Exception:
            pass
        r = requests.post(ai_config.model_url(model), json=payload,
                          headers=headers, stream=True, timeout=180)
        r.raise_for_status()
        # ⚠️ 必须显式指定 utf-8：text/event-stream 没有 charset 时 requests 默认
        # ISO-8859-1，会把中文 UTF-8 字节解成乱码（实测「赚积分」→「èµç§¯å」）。
        r.encoding = "utf-8"
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("data: "):
                raw = raw[6:]
            if raw.strip() == "[DONE]":
                break
            try:
                d = json.loads(raw)
            except Exception:
                continue
            delta = (d.get("choices") or [{}])[0].get("delta") or {}
            # 只取正文；reasoning_content（思考流）跳过
            piece = delta.get("content") or ""
            if piece:
                yield piece
    except Exception:
        yield "\n[连接中断，请稍后再试]"


def chat_once(messages):
    """非流式（备用/测试用）。走当前配置的模型。"""
    import ai_config
    model = "glm-z1-air"
    headers = {
        "Authorization": "Bearer " + ai_config.get_ai_key(model),
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": ai_config.MODELS[model]["max_tokens"],
        "temperature": ai_config.MODELS[model]["temperature"],
    }
    try:
        payload.update(ai_config.model_extra_body(model, thinking=False))
    except Exception:
        pass
    r = requests.post(ai_config.model_url(model), json=payload,
                      headers=headers, timeout=180)
    r.raise_for_status()
    d = r.json()
    return _clean(d["choices"][0]["message"]["content"])


def available():
    """客服可用性 = 模型 key 能取到（走云端，不依赖本地模型）。"""
    try:
        import ai_config
        return bool(ai_config.get_ai_key("glm-z1-air"))
    except Exception:
        return False
