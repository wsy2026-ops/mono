# 安全策略

## 报告漏洞

**请不要开公开 issue。** 用下面任一方式私下说：

- GitHub 的 [Private vulnerability reporting](../../security/advisories/new)（推荐）
- 邮件：`<填你的邮箱>`

尽量写清楚：影响哪个页面/接口、复现步骤、你期望的行为。有 PoC 更好。

一般 3 天内回复。修好之后我会在 release notes 里说明，除非你要求匿名。

## 这个项目里值得关注的地方

如果你在审代码，这几个位置是安全敏感区：

| 位置 | 风险 |
|---|---|
| `admin_secret.py` + `/become-admin` | 拿到通行证就能提权成超管。通行证从环境变量读，**不设就拒绝** |
| `oj/` 判题机 | 跑用户提交的代码，靠子进程 + 资源限制隔离，不是容器级隔离 |
| `moderation.py` | 内容审核规则，绕过方式值得关注 |
| `pg_shadow.py` | 影子库连接串从环境变量读 |
| 上传相关 | 文件类型/大小校验，路径拼接 |
| 模板里的 `|safe` | 任何 `|safe` 都意味着「这段 HTML 是可信的」，改之前想清楚 |

## 已知的设计取舍（不是漏洞）

- **判题机不是沙箱**：用 `resource` 限制 CPU/内存 + 超时，没有 seccomp/namespace。
  给自己人用的 OJ，不对外开注册就没问题；要对外开放请自己加容器隔离。
- **SQLite 单文件**：并发写靠 `busy_timeout`，不是真正的并发安全。
  量大了请换 PostgreSQL（`pg_shadow.py` 里有迁移路径）。
- **没有 CSRF token**：依赖 `SameSite=Lax` cookie + 关键操作走 POST。
  如果你的部署跨站嵌入，请自己加 token。

## 支持的版本

只有 `main` 分支在维护。
