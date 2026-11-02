# infra/ —— 基础设施即代码（104）

服务器配置纳入版本管理。改配置 = 改这里的文件 + `apply.sh`，不再靠 ssh 上去手改。

## 为什么有这目录

2026-11-02 凌晨发现一件事：**站点每天 00:00 会被自己杀掉一次**。

```
/etc/logrotate.d/mono 的 postrotate 里有：
    systemctl kill -s HUP mono
而 Python 默认不处理 SIGHUP → 进程直接退出
systemd Restart=always 5 秒后拉起 → 用户看到 502
```

同一份配置还有两个毛病：
- 重复列了 `/var/log/nginx/mono-*.log`，而 `/etc/logrotate.d/nginx` 用 `*.log` 通配
  → logrotate 报 duplicate，**整个 nginx 配置被跳过**，nginx 日志其实一直没轮转
- 真正的大文件 `/opt/mono/app/server.log`（33MB）**根本没在列表里**

三处都是「手工改配置、没人 review、没有版本管理」的典型后果。

## 文件

| 文件 | 下发到 | 说明 |
|---|---|---|
| `logrotate/mono` | `/etc/logrotate.d/mono` | **会下发**。用 copytruncate，不发 HUP |
| `nginx/sites-available-mono` | `/etc/nginx/sites-available/mono` | **会下发**。先 `nginx -t` 再 reload |
| `nginx/snippets-mono-locations.conf` | `/etc/nginx/snippets/` | **会下发**。共享 location |
| `systemd/mono.service` | —— | **不下发**，脱敏快照，只用于对比漂移 |

⚠️ systemd 单元里有关键密钥（SECRET_KEY / RESEND / SMTP / Neon DSN），
所以 `apply.sh` 故意跳过它。改服务配置请直接改服务器，再跑 `snapshot.sh` 更新快照。

## 用法

```bash
bash infra/snapshot.sh        # 拉线上配置 → 本地（自动脱敏）
bash infra/apply.sh --check   # 只看差异，不落盘
bash infra/apply.sh           # 下发 + 校验（自动备份到 /root/infra-backup-<ts>/）
bash infra/verify.sh          # 只做部署后校验（也可以 ssh 过去跑）
```

`verify.sh` 检查 7 项：接口探活、服务与端口、数据一致性（表数/users/可见帖子）、
资源占用（磁盘/内存/负载）、**logrotate 配置（有没有 HUP 自杀）**、定时任务、引擎僵尸进程。
退出码 0/1，可以直接当 CI 门槛。

## 回滚

`apply.sh` 每次下发前会把线上配置备份到 `/root/infra-backup-<时间戳>/`：

```bash
ssh mono-vps "cp -a /root/infra-backup-20261102_010000/* /etc/logrotate.d/ && systemctl reload nginx"
```

## 还没做的（104 剩下的）

- **部署失败自动回滚**：现在 `sync_server.sh` 校验失败只报错，不回滚代码。
  真要做得先给 `/opt/mono/app` 做 git tag + 一键 checkout 上一版。
- **新增机器一键初始化**：`apply.sh` 只覆盖配置，不装依赖（nginx/systemd unit 的 enable）。
  加机器时还得手工来一遍——但至少配置有据可查了。
