# -*- coding: utf-8 -*-
"""cold_guard.py — 053 审核第一机（HK 常驻，onnxruntime int8，零 torch）。

模型：thu-coai/roberta-base-cold（实为中文 BERT，BertForSequenceClassification），
int8 量化 98MB，中文冒犯检测准确率 82.75%（清华 EMNLP2022）。

设计：
  - 常驻内存 ~150MB（onnxruntime + 98MB 模型），HK 2C2G 无压力
  - judge(text) → {"offensive": bool, "score": float, "level": "ok"/"suspect"/"bad"}
  - 阈值：offensive 概率 >=0.7 → bad（违规）；0.5~0.7 → suspect（争议，送 LA 复核）；
    <0.5 → ok
  - 线程安全：onnxruntime InferenceSession 本身线程安全，tokenizer 只读
  - 惰性加载：首次 judge 时才加载（进程启动不拖慢）
"""
import os
import threading

MODEL_DIR = os.environ.get("MONO_COLD_MODEL_DIR") or "/opt/mono-ai/models/hk-cold"

_lock = threading.Lock()
_sess = None
_tok = None
_failed = False

BAD_TH = 0.70      # >= 违规（自动隐藏）
# >= 争议（进人工复核队列）。实测分布：正常文本 0.01~0.07，脏话 0.20~0.93。
# 原阈值 0.5 太保守 ——「你个废物，滚出这个社区」只判 0.20 会直接放行，
# 降到 0.15 能接住这类漏网，且离正常文本的上沿（0.07）仍有 2 倍余量。
SUSPECT_TH = 0.15


def available():
    """模型文件齐不齐（不加载）。"""
    if _failed:
        return False
    need = ("model.onnx", "vocab.txt", "tokenizer_config.json")
    return all(os.path.exists(os.path.join(MODEL_DIR, f)) for f in need)


def _load():
    """惰性加载（双检锁）。返回 (sess, tok) 或 (None, None)。"""
    global _sess, _tok, _failed
    if _sess is not None:
        return _sess, _tok
    with _lock:
        if _sess is not None:
            return _sess, _tok
        if _failed:
            return None, None
        try:
            import onnxruntime as ort
            from transformers import BertTokenizer
            # 限制线程数：HK 2 核，别让推理抢光 CPU
            so = ort.SessionOptions()
            so.intra_op_num_threads = 1
            so.inter_op_num_threads = 1
            _sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model.onnx"), so)
            _tok = BertTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
            return _sess, _tok
        except Exception:
            _failed = True
            return None, None


def judge(text):
    """判定一段文本。返回 dict：
    {"offensive": bool, "score": float, "level": "ok"|"suspect"|"bad", "why": str}

    模型不可用时返回 level="skip"（调用方按「未审核」处理，不阻断）。
    """
    t = (text or "").strip()
    if not t:
        return {"offensive": False, "score": 0.0, "level": "ok", "why": ""}
    sess, tok = _load()
    if sess is None:
        return {"offensive": False, "score": 0.0, "level": "skip",
                "why": "cold 模型不可用"}
    try:
        import numpy as np
        enc = tok(t[:400], return_tensors="np")  # 截断 400 字，审核够用
        inputs = {k: v.astype(np.int64) for k, v in enc.items()}
        logits = sess.run(None, inputs)[0][0]
        # softmax → 违规概率
        e = np.exp(logits - logits.max())
        p = float((e / e.sum())[1])
        if p >= BAD_TH:
            level, why = "bad", "模型判违规（%.2f）" % p
        elif p >= SUSPECT_TH:
            level, why = "suspect", "模型判争议（%.2f）" % p
        else:
            level, why = "ok", ""
        return {"offensive": p >= BAD_TH, "score": round(p, 4),
                "level": level, "why": why}
    except Exception as e:
        return {"offensive": False, "score": 0.0, "level": "skip",
                "why": "推理异常：%s" % str(e)[:80]}
