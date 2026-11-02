import json
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sandbox import Sandbox


HOST = "127.0.0.1"
PORT = 8081
PROBLEMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "problems")

sandbox = Sandbox()
tasks = {}
lock = threading.Lock()
work_queue = queue.Queue()
STARTED_AT = time.time()
STATS = {"total": 0, "ac": 0, "wa": 0, "tle": 0, "mle": 0, "re": 0, "ce": 0, "se": 0}
MAX_TASKS = 500

ORDER = ["SE", "CE", "MLE", "TLE", "OLE", "RE", "WA", "AC"]


def load_problem(pid):
    d = os.path.join(PROBLEMS_DIR, str(pid))
    meta_path = os.path.join(d, "problem.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    cfg = meta.get("cases") or {}

    tests = []
    idx = 1
    while True:
        ip = os.path.join(d, f"{idx}.in")
        op = os.path.join(d, f"{idx}.out")
        if not os.path.isfile(ip) or not os.path.isfile(op):
            break
        with open(ip, encoding="utf-8") as f:
            inp = f.read()
        with open(op, encoding="utf-8") as f:
            out = f.read()
        c = cfg.get(str(idx)) or {}
        tests.append({
            "index": idx,
            "input": inp,
            "expected": out,
            "time_ms": int(c.get("time_ms") or 0),
            "memory_mb": int(c.get("memory_mb") or 0),
            "score": int(c.get("score") or 0),
        })
        idx += 1

    if not tests:
        return None
    meta["tests"] = tests
    meta["id"] = str(pid)
    return meta


def normalize(s):
    lines = [l.rstrip() for l in (s or "").replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def update(tid, **kw):
    with lock:
        if tid in tasks:
            tasks[tid].update(kw)
        if len(tasks) > MAX_TASKS:
            for k in list(tasks.keys()):
                if len(tasks) <= MAX_TASKS:
                    break
                if tasks[k].get("status") == "done":
                    tasks.pop(k, None)


LANGS = {
    "c++98": "C++98",
    "c++11": "C++11",
    "c++14": "C++14",
    "c++17": "C++17",
    "c++20": "C++20",
}


def judge(tid):
    task = tasks.get(tid)
    if not task:
        return

    update(tid, status="compiling")
    problem = load_problem(task["problem_id"])
    if not problem:
        update(tid, status="done", verdict="SE", message="题目不存在")
        return

    time_limit = int(problem.get("time_limit_ms", 1000))
    memory_limit = int(problem.get("memory_limit_mb", 256))
    tests = problem["tests"]

    update(tid, status="running", total=len(tests))

    try:
        r = sandbox.judge(
            code=task.get("_code") or "",
            inputs=[t["input"] for t in tests],
            time_limit_ms=time_limit,
            memory_limit_mb=memory_limit,
            std=task.get("_std") or "c++14",
            o2=bool(task.get("_o2", True)),
            limits=[{"time_ms": t.get("time_ms") or time_limit,
                     "memory_mb": t.get("memory_mb") or memory_limit}
                    for t in tests],
        )
    except Exception as e:
        update(tid, status="done", verdict="SE", message=str(e)[:400])
        return

    if r.error:
        update(tid, status="done", verdict="SE", message=r.error)
        return

    if not r.compiled:
        update(tid, status="done", verdict="CE", message=r.compile_message)
        return

    results = []
    worst = "AC"
    max_time = 0
    max_mem = 0
    got_score = 0
    full_score = sum((t.get("score") or 0) for t in tests)
    if full_score <= 0:
        full_score = len(tests) * 100

    for i, t in enumerate(tests):
        mem = 0
        if i >= len(r.cases):
            v, ms, code = "SE", 0, -1
        else:
            c = r.cases[i]
            ms = c.time_ms
            code = c.exit_code
            mem = c.memory_kb
            max_time = max(max_time, ms)
            max_mem = max(max_mem, mem)

            if code in (143, 124):
                v = "TLE"
            elif code == 137:
                v = "MLE"
            elif code == 0:
                got = normalize(c.stdout)
                want = normalize(t["expected"])
                v = "AC" if got == want else "WA"
            else:
                v = "RE"

            if v == "AC" and ms > (int(t.get("time_ms") or 0) or time_limit) + 200:
                v = "TLE"

        this_score = int(t.get("score") or 0)
        if this_score <= 0:
            this_score = max(1, round(100.0 / max(1, len(tests))))
        if v == "AC":
            got_score += this_score

        results.append({
            "index": t["index"],
            "verdict": v,
            "time_ms": ms,
            "memory_kb": mem,
            "exit_code": code,
            "score": this_score,
            "got": this_score if v == "AC" else 0,
        })

        if ORDER.index(v) < ORDER.index(worst):
            worst = v

    with lock:
        STATS["total"] += 1
        key = (worst or "SE").lower()
        if key in STATS:
            STATS[key] += 1

    update(tid, status="done", verdict=worst,
           score=got_score, full_score=full_score,
           time_ms=max_time, memory_kb=max_mem, wall_ms=r.wall_ms,
           compile_ms=r.compile_ms, run_ms=r.run_ms, warm=r.warm,
           tests=results,
           passed=sum(1 for x in results if x["verdict"] == "AC"),
           total=len(tests))


def worker():
    while True:
        tid = work_queue.get()
        try:
            judge(tid)
        except Exception as e:
            update(tid, status="done", verdict="SE", message=str(e)[:400])
        finally:
            with lock:
                if tid in tasks:
                    tasks[tid].pop("_code", None)
                    tasks[tid].pop("_std", None)
                    tasks[tid].pop("_o2", None)
            work_queue.task_done()


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/health":
            st = {}
            try:
                st = sandbox.stats()
            except Exception:
                pass
            running = False
            try:
                running = sandbox._running(sandbox.cid) if sandbox.cid else False
            except Exception:
                pass
            with lock:
                recent = sorted(tasks.values(),
                                key=lambda t: t.get("created_at") or 0,
                                reverse=True)[:5]
            self._send(200, {
                "ok": True,
                "queue": work_queue.qsize(),
                "tasks": len(tasks),
                "container": st.get("container"),
                "container_running": running,
                "uses": st.get("uses"),
                "max_uses": st.get("max_uses"),
                "uptime": int(time.time() - STARTED_AT),
                "image": sandbox.image,
                "workers": 1,
                "languages": LANGS,
                "stats": dict(STATS),
                "recent": [{
                    "id": t.get("id"),
                    "problem_id": t.get("problem_id"),
                    "status": t.get("status"),
                    "verdict": t.get("verdict"),
                    "time_ms": t.get("time_ms"),
                } for t in recent],
            })
            return

        if self.path == "/problems":
            out = []
            if os.path.isdir(PROBLEMS_DIR):
                names = sorted(os.listdir(PROBLEMS_DIR),
                               key=lambda x: (len(x), x))
                for name in names:
                    p = load_problem(name)
                    if p:
                        out.append({
                            "id": p["id"],
                            "title": p.get("title", ""),
                            "description": p.get("description", ""),
                            "time_limit_ms": p.get("time_limit_ms", 1000),
                            "memory_limit_mb": p.get("memory_limit_mb", 256),
                            "test_count": len(p["tests"]),
                        })
            self._send(200, {"ok": True, "problems": out})
            return

        if self.path.startswith("/status/"):
            tid = self.path.split("/", 2)[-1]
            with lock:
                t = tasks.get(tid)
            if not t:
                self._send(404, {"ok": False, "msg": "任务不存在"})
                return
            self._send(200, {"ok": True, "task": t})
            return

        self._send(404, {"ok": False, "msg": "not found"})

    def do_POST(self):
        if self.path != "/judge":
            self._send(404, {"ok": False, "msg": "not found"})
            return

        data = self._read()
        code = data.get("code") or ""
        pid = data.get("problem_id")
        std = (data.get("std") or "c++14").lower()
        o2 = data.get("o2")
        if o2 is None:
            o2 = True
        if std not in LANGS:
            std = "c++14"

        if not code.strip():
            self._send(400, {"ok": False, "msg": "代码为空"})
            return
        if len(code) > 65536:
            self._send(400, {"ok": False, "msg": "代码过长（上限 64KB）"})
            return
        if pid is None:
            self._send(400, {"ok": False, "msg": "缺少 problem_id"})
            return

        tid = uuid.uuid4().hex[:16]
        with lock:
            tasks[tid] = {
                "id": tid,
                "problem_id": pid,
                "status": "pending",
                "verdict": None,
                "code_len": len(code),
                "created_at": time.time(),
                "std": std,
                "o2": bool(o2),
                "_code": code,
                "_std": std,
                "_o2": bool(o2),
            }
        work_queue.put(tid)
        self._send(200, {"ok": True, "task_id": tid})


def main():
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"judge server on http://{HOST}:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
