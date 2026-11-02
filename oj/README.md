# MONO OJ 判题机

独立判题服务，跑在服务器 `127.0.0.1:8081`，只监听本机（外部访问不到）。

## 架构

```
MONO (Flask)  ──HTTP──>  判题服务 :8081  ──docker run──>  沙箱容器
                              │
                              ├─ 串行队列（2 核机器，一次只判一个）
                              ├─ 编译：容器内 g++ -O2 -std=c++17
                              ├─ 执行：容器内跑，限时限内存
                              └─ 比对：逐行比对（忽略行尾空格）
```

## 文件

```
/opt/mono/oj/
  ├─ judge_server.py      HTTP 服务（Python 标准库，零依赖）
  ├─ sandbox.py           Docker 沙箱封装
  └─ problems/            题目数据
       └─ 1/
           ├─ problem.json
           ├─ 1.in / 1.out
           ├─ 2.in / 2.out
           └─ ...
```

## 接口

### 提交判题

```
POST http://127.0.0.1:8081/judge
Content-Type: application/json

{
  "code": "#include <iostream>...",
  "problem_id": 1
}

返回：
{ "ok": true, "task_id": "a1b2c3d4e5f6g7h8" }
```

### 查询结果

```
GET http://127.0.0.1:8081/status/<task_id>

返回：
{
  "ok": true,
  "task": {
    "id": "a1b2c3d4e5f6g7h8",
    "problem_id": 1,
    "status": "done",          // pending / compiling / running / done
    "verdict": "AC",           // AC / WA / TLE / MLE / RE / CE / OLE / SE
    "time_ms": 644,
    "passed": 5,
    "total": 5,
    "progress": 5,             // 正在跑第几个测试点
    "compile_ms": 420,
    "tests": [
      { "index": 1, "verdict": "AC", "time_ms": 3, "exit_code": 0 },
      ...
    ],
    "message": "编译错误信息（CE 时才有）"
  }
}
```

### 健康检查

```
GET http://127.0.0.1:8081/health
{
  "ok": true, "queue": 0, "tasks": 0,
  "container": "eb00dbbf...",   // 常驻容器 ID
  "uses": 6,                    // 已判题次数
  "max_uses": 200               // 到 200 次自动重建容器
}
```

### 题目列表

```
GET http://127.0.0.1:8081/problems
{
  "ok": true,
  "problems": [
    { "id": "1", "title": "A+B Problem", "time_limit_ms": 1000, "test_count": 5 }
  ]
}
```

## 性能（实测）

**容器预热**：判题服务启动时起一个常驻容器（`mono-judge-pool`），
判题时用 `docker exec` 进去跑，省掉每次 `docker run` 的启动开销。
每 200 次判题自动重建容器（防止用户代码残留污染）。

| 场景 | 端到端耗时 |
|---|---|
| **首次判题（冷启动）** | 约 2.2 秒 |
| **后续判题（预热）** | **1.2 ~ 1.7 秒** |

**耗时构成**（预热后）：

| 阶段 | 耗时 |
|---|---|
| 编译 | **1.1 ~ 1.7 秒** ← 现在的大头 |
| 运行（5 个测试点） | 0.04 ~ 0.1 秒 |
| 容器启动 | 0（已预热） |

**时间字段说明**：

| 字段 | 含义 |
|---|---|
| **`time_ms`** | **程序实际运行时间**（单个测试点最慢的那个）—— 给用户看的 |
| **`compile_ms`** | 编译段耗时（含容器 exec 开销） |
| **`run_ms`** | 所有测试点运行时间之和 |
| **`wall_ms`** | 判题机内部总耗时 |
| **`warm`** | 这次是否用了预热容器 |

**怎么测出真实运行时间**：容器内用 `date +%s%N` 掐表，只包住程序执行那一行，
不含容器启动、编译、文件读写。

## 判定说明

| 结果 | 含义 | 怎么判出来的 |
|---|---|---|
| **AC** | 通过 | 所有测试点输出匹配 |
| **WA** | 答案错误 | 输出不匹配 |
| **TLE** | 超时 | `timeout` 用 SIGTERM 杀（退出码 143） |
| **MLE** | 内存超限 | Docker OOM 杀（退出码 137） |
| **RE** | 运行时错误 | 段错误(139) / 除零(136) / 其他非 0 |
| **CE** | 编译错误 | g++ 返回非 0，错误信息回传 |
| **OLE** | 输出超限 | 输出超过 1 MB |
| **SE** | 系统错误 | 判题机自身异常 |

**关键点**：`timeout` 用 **SIGTERM**（143），Docker OOM 用 **SIGKILL**（137），
这样能区分「超时」和「内存爆掉」—— 两者都返回 137 的话就分不清了。

## 沙箱限制

每次运行都用 `docker run` 起一个干净容器：

| 限制 | 参数 | 值 |
|---|---|---|
| 网络 | `--network none` | **完全断网** |
| CPU | `--cpus 1` | 1 核 |
| 内存 | `--memory` | 256 MB（题目可配） |
| 内存 + swap | `--memory-swap` | 同上（**禁用 swap**） |
| 进程数 | `--pids-limit 64` | 防 fork 炸弹 |
| 根文件系统 | `--read-only` | 只读 |
| 临时目录 | `--tmpfs /tmp:size=64m` | 可写，64MB |
| 工作目录 | `-v <box>:/box` | 唯一可写挂载点 |
| 编译内存 | `--memory 512m` | 编译时给宽一点 |

**镜像**：`mono-judge:cpp`（基于 `ubuntu:22.04` + `g++`，约 309 MB）

**常驻容器**：`mono-judge-pool`
- 启动参数：`--init` / `--network none` / `--cpus 1` / `--memory 768m` /
  `--memory-swap 768m` / `--pids-limit 128` / `-v /var/lib/mono-oj:/work`
- `--init` 是必需的：容器 PID 1 是 `sleep infinity`，它不回收僵尸进程，
  跑二十多次判题后僵尸会堆到 `--pids-limit` 上限，之后所有提交都报
  `OCI runtime exec failed: procReady not received`
- 内存只给 768m：这台 VPS 一共 2GB，给 1280m 会被压到 OOM
- 单次运行的内存限制靠容器内 `ulimit -v`（容器级 `--memory` 是共享的）
- 工作目录用 UUID 命名，判完即删
- 每跑满 `MAX_USES`（100）次自动重建容器

**改了 `mono-run.c` 之后要重建镜像**（它编译进镜像的 `/usr/local/bin/mono-run`）：

```bash
scp oj/mono-run.c mono-vps:/tmp/mono-run.c
ssh mono-vps "cd /tmp && gcc -O2 -static -o mono-run-new mono-run.c"
ssh mono-vps "docker cp /tmp/mono-run-new mono-judge-pool:/usr/local/bin/mono-run"
ssh mono-vps "docker commit mono-judge-pool mono-judge:cpp"
ssh mono-vps "docker rm -f mono-judge-pool && systemctl restart mono-oj"   # 用新镜像重建容器
```

⚠️ **必须加 `-static`**：宿主是 Ubuntu 22.04（glibc 2.35），镜像是 Debian 系（glibc 更旧），
动态链接的二进制在容器里会报 `failed to run command: No such file or directory`。

⚠️ **`mono-run` 必须处理 SIGTERM**：`timeout` 的超时信号是发给 `mono-run` 的，
如果它没有信号处理器就会直接死掉 —— 既不打印 `@@@R` 计时行（每点耗时全变成 1ms），
也不杀掉它 fork 出来的程序（留下空转进程）。
现在的做法：收到信号先 `SIGKILL` 子进程、`wait4` 要在 `EINTR` 时重试、
打印完 `@@@R` 之后再按默认行为重新 `raise` 那个信号，保住 124/143 的退出码。

## 加新题

在 `problems/` 下建目录，编号递增：

```
problems/3/
  problem.json     { "title": "...", "time_limit_ms": 1000, "memory_limit_mb": 256 }
  1.in
  1.out
  2.in
  2.out
```

**测试点从 `1.in` / `1.out` 开始编号，必须连续**，遇到缺失就停止读取。

改完传到服务器：

```bash
cd E:/9.2/文件夹/test6
tar czf oj.tgz oj
scp oj.tgz mono-vps:/tmp/
ssh mono-vps "cd /opt/mono && tar xzf /tmp/oj.tgz && rm -f /tmp/oj.tgz && systemctl restart mono-oj"
```

## 运维

```bash
systemctl status mono-oj        # 状态
systemctl restart mono-oj       # 重启
journalctl -u mono-oj -n 50     # 日志（systemd）
tail -50 /var/log/mono-oj.log   # 日志（文件）

docker ps                       # 看有没有卡住的容器
docker images                   # 看镜像
```

**临时目录**：`/var/lib/mono-oj/`（判完自动清理）

## 已知限制

1. **只支持 C++**（要加 Python 就在 `sandbox.py` 里加 `compile_python`）
2. **串行判题**（2 核机器，一次只跑一个，避免吃满 CPU 拖慢网站）
3. **内存峰值不准**（只记录是否 OOM，没记实际峰值 —— 要精确得读 cgroup）
4. **任务存在内存里**（判题机重启后历史记录丢失，要持久化得接 SQLite）
5. **没有用户鉴权**（只监听 127.0.0.1，MONO 调它时不用带 token）

## 和 MONO 集成（还没做）

MONO 那边要加的东西：

1. **数据库表**：`oj_problems`（题目元信息）、`oj_submissions`（提交记录）
2. **路由**：`/oj`（题目列表）、`/oj/<id>`（题目详情 + 提交框）、`/oj/status/<task_id>`（轮询结果）
3. **代理接口**：MONO 收到提交 → 转发给 `127.0.0.1:8081/judge` → 把 `task_id` 存库
4. **前端**：代码编辑器（可用 `CodeMirror`）、轮询结果、显示 verdict
