import math
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import List


IMAGE = "mono-judge:cpp"
BOX_ROOT = "/var/lib/mono-oj"
CONTAINER = "mono-judge-pool"
MARK = "@@@MONO@@@"
MAX_USES = 100
BOX_MEM = "768m"
BROKEN_HINTS = ("procReady not received", "OCI runtime exec failed",
                "container process", "is not running")


@dataclass
class CaseResult:
    index: int
    exit_code: int
    time_ms: int
    stdout: str
    stderr: str = ""
    wall_ms: int = 0
    memory_kb: int = 0


@dataclass
class JudgeResult:
    compiled: bool = True
    compile_message: str = ""
    wall_ms: int = 0
    compile_ms: int = 0
    run_ms: int = 0
    warm: bool = False
    cases: List[CaseResult] = field(default_factory=list)
    error: str = ""


class Sandbox:

    def __init__(self, image: str = IMAGE, box_root: str = BOX_ROOT):
        self.image = image
        self.box_root = box_root
        self.cid = None
        self.uses = 0
        os.makedirs(box_root, exist_ok=True)
        self._ensure()

    def _running(self, cid):
        try:
            r = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", cid],
                capture_output=True, text=True, timeout=10, errors="replace")
            return r.stdout.strip() == "true"
        except Exception:
            return False

    def _kill(self, cid):
        try:
            subprocess.run(["docker", "rm", "-f", cid],
                           capture_output=True, timeout=20)
        except Exception:
            pass

    def _ensure(self):
        if self.cid and self.uses < MAX_USES and self._running(self.cid):
            return self.cid

        if self.cid:
            self._kill(self.cid)

        try:
            subprocess.run(["docker", "rm", "-f", CONTAINER],
                           capture_output=True, timeout=20)
        except Exception:
            pass

        r = subprocess.run(
            ["docker", "run", "-d",
             "--name", CONTAINER,
             "--init",
             "--network", "none",
             "--cpus", "1",
             "--memory", BOX_MEM,
             "--memory-swap", BOX_MEM,
             "--pids-limit", "128",
             "-v", f"{self.box_root}:/work",
             "-w", "/work",
             self.image,
             "sleep", "infinity"],
            capture_output=True, text=True, timeout=60, errors="replace")

        if r.returncode != 0:
            raise RuntimeError("容器启动失败: " + (r.stderr or "")[:300])

        self.cid = r.stdout.strip()
        self.uses = 0
        return self.cid

    def stats(self):
        return {"container": self.cid, "uses": self.uses, "max_uses": MAX_USES}

    def _broken(self, r):
        blob = (r.stdout or "") + "\n" + (r.stderr or "")
        if MARK in blob:
            return False
        return any(h in blob for h in BROKEN_HINTS) or not blob.strip()

    def judge(self, code: str, inputs: List[str],
              time_limit_ms: int = 1000, memory_limit_mb: int = 256,
              extra_timeout: int = 20,
              std: str = "c++14", o2: bool = True,
              limits: List[dict] = None) -> JudgeResult:

        warm = self.cid is not None and self.uses > 0
        t_all = time.time()

        try:
            cid = self._ensure()
        except Exception as e:
            res = JudgeResult()
            res.error = str(e)[:300]
            return res

        name = uuid.uuid4().hex
        host_dir = os.path.join(self.box_root, name)
        box = f"/work/{name}"
        os.makedirs(host_dir, exist_ok=True)
        os.chmod(host_dir, 0o777)

        try:
            with open(os.path.join(host_dir, "main.cpp"), "w", encoding="utf-8") as f:
                f.write(code)
            for i, inp in enumerate(inputs, 1):
                with open(os.path.join(host_dir, f"{i}.in"), "w", encoding="utf-8") as f:
                    f.write(inp)

            n = len(inputs)

            secs, kbs = [], []
            for i in range(n):
                lim = (limits or [])[i] if limits and i < len(limits) else {}
                t = int(lim.get("time_ms") or time_limit_ms)
                m = int(lim.get("memory_mb") or memory_limit_mb)
                secs.append("%.3f" % max(0.1, t / 1000.0))
                kbs.append(str(max(16, m) * 1024))
            max_sec = int(math.ceil(max(float(s) for s in secs))) if secs else 1
            with open(os.path.join(host_dir, "__sec.txt"), "w") as f:
                f.write("\n".join(secs) + "\n")
            with open(os.path.join(host_dir, "__kb.txt"), "w") as f:
                f.write("\n".join(kbs) + "\n")

            std = (std or "c++14").lower().replace("c++", "c++")
            if std not in ("c++98", "c++03", "c++11", "c++14", "c++17", "c++20", "c++23"):
                std = "c++14"
            opt = "-O2" if o2 else "-O0"
            gflags = f"g++ {opt} -std={std} -o main main.cpp"

            script = "\n".join([
                f"{gflags} 2> __ce.txt",
                "if [ $? -ne 0 ]; then",
                f"  echo '{MARK}COMPILE_FAIL'",
                "  cat __ce.txt",
                f"  echo '{MARK}END'",
                "  exit 0",
                "fi",
                f"echo '{MARK}COMPILE_OK'",
                f"for i in $(seq 1 {n}); do",
                f"  echo '{MARK}TEST_BEGIN'",
                f"  CSEC=$(sed -n \"${{i}}p\" __sec.txt)",
                f"  CKB=$(sed -n \"${{i}}p\" __kb.txt)",
                f"  ( ulimit -v $CKB; ulimit -S -s unlimited; exec timeout -s TERM -k 1 $CSEC "
                f"mono-run ./main < $i.in ) > __o.txt 2> __e.txt",
                "  RC=$?",
                "  TL=$(grep '^@@@R ' __e.txt | sed 's/^@@@R //')",
                f"  echo '{MARK}EXIT ' $RC $TL",
                f"  echo '{MARK}OUT_BEGIN'",
                "  cat __o.txt",
                f"  echo '{MARK}OUT_END'",
                f"  echo '{MARK}ERR_BEGIN'",
                "  cat __e.txt",
                f"  echo '{MARK}ERR_END'",
                "done",
                f"echo '{MARK}END'",
            ])

            tmo = extra_timeout + n * (max_sec + 3) + 15
            r = subprocess.run(
                ["docker", "exec", "-w", box, cid, "sh", "-c", script],
                capture_output=True, text=True, timeout=tmo, errors="replace")

            if self._broken(r):
                self._kill(cid)
                self.cid = None
                cid = self._ensure()
                self.uses = 0
                r = subprocess.run(
                    ["docker", "exec", "-w", box, cid, "sh", "-c", script],
                    capture_output=True, text=True, timeout=tmo, errors="replace")

            if self._broken(r):
                res = JudgeResult()
                res.error = "判题容器异常，已自动重建，请重新提交一次"
                return res

            wall_ms = int((time.time() - t_all) * 1000)
            res = self._parse(r.stdout or "", n, wall_ms)
            res.warm = warm
            self.uses += 1
            return res

        except subprocess.TimeoutExpired:
            res = JudgeResult()
            res.error = "判题机超时（程序可能卡死）"
            return res
        except Exception as e:
            res = JudgeResult()
            res.error = str(e)[:300]
            return res
        finally:
            shutil.rmtree(host_dir, ignore_errors=True)

    def _parse(self, out: str, n: int, wall_ms: int) -> JudgeResult:
        res = JudgeResult(wall_ms=wall_ms)

        if f"{MARK}COMPILE_FAIL" in out:
            res.compiled = False
            i = out.find(f"{MARK}COMPILE_FAIL") + len(f"{MARK}COMPILE_FAIL")
            j = out.find(f"{MARK}END", i)
            res.compile_message = (out[i:j].strip() if j > 0 else out[i:].strip())[:4000]
            return res

        if f"{MARK}COMPILE_OK" not in out:
            res.compiled = False
            res.compile_message = "编译阶段异常\n" + out[:2000]
            return res

        cursor = out.find(f"{MARK}COMPILE_OK")
        first_case_start = None

        for idx in range(1, n + 1):
            tb = out.find(f"{MARK}TEST_BEGIN", cursor)
            if tb < 0:
                break
            if first_case_start is None:
                first_case_start = tb
            body = out[tb:]

            ep = body.find(f"{MARK}EXIT ")
            if ep < 0:
                break
            parts = body[ep + len(f"{MARK}EXIT "):].split("\n", 1)[0].split()
            try:
                code = int(parts[0])
                us = int(parts[1]) if len(parts) > 1 else 0
                mem = int(parts[2]) if len(parts) > 2 else 0
                ms = max(1, int(round(us / 1000.0)))
                wall_ms_case = ms
                mem_kb = mem
            except (ValueError, IndexError):
                code, ms, wall_ms_case, mem_kb = -1, 0, 0, 0

            ob = body.find(f"{MARK}OUT_BEGIN")
            oe = body.find(f"{MARK}OUT_END")
            stdout = body[ob + len(f"{MARK}OUT_BEGIN"):oe].lstrip("\n") if ob >= 0 and oe > ob else ""

            eb = body.find(f"{MARK}ERR_BEGIN")
            ee = body.find(f"{MARK}ERR_END")
            stderr = body[eb + len(f"{MARK}ERR_BEGIN"):ee].lstrip("\n") if eb >= 0 and ee > eb else ""
            stderr = "\n".join(l for l in stderr.split("\n") if not l.startswith("@@@R "))

            res.cases.append(CaseResult(idx, code, ms, stdout, stderr,
                                        wall_ms=wall_ms_case, memory_kb=mem_kb))
            cursor = tb + 1

        res.run_ms = sum(c.time_ms for c in res.cases)
        if first_case_start is not None:
            res.compile_ms = max(0, wall_ms - res.run_ms)
        else:
            res.compile_ms = wall_ms

        return res


def make_box() -> str:
    d = os.path.join(BOX_ROOT, uuid.uuid4().hex)
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o777)
    return d


def clean_box(d: str) -> None:
    shutil.rmtree(d, ignore_errors=True)
