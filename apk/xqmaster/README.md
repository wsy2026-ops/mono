# 象棋大师 · 独立 APK

把「中国象棋大师」单独打成一个 App，不含社区其他页面。

## 为什么这么做，而不是直接构建

本机**没有 Android SDK**（`aapt2` / `d8` / `apksigner` 都没有），
用命令行构建不出能装的 APK。而你现在用的 **HBuilderX 云打包**是现成能跑通的流水线，
所以做法是：**复制一份现成工程 → 换掉启动页 → 改两处配置 → 云打包**。

## 步骤

### 1. 复制工程

把 `D:/MONO/MONO/` 整个复制一份到 `D:/MONO/XQMASTER/`。
（manifest.json 是验证过能用的，别从零写。）

### 2. 换启动页

用本目录的 `index.html` 覆盖 `D:/MONO/XQMASTER/index.html`。

它做两件事：先播「陆柒工作室」，再播棋局开场（棋盘网格淡入 → 帅印砸下 → 标题浮现），
最后 `location.replace('https://mono-craft.de5.net/game/xqmaster')`。

**注意**：游戏页需要登录，未登录会被带到 `/login?next=/game/xqmaster`，
登录完成后**自动回到游戏页**（这一条是本轮刚补的 `next` 支持）。

### 3. 改 manifest.json

打开 `D:/MONO/XQMASTER/manifest.json`，改这三处：

| 字段 | 改成 |
|---|---|
| `name` | `象棋大师` |
| `version.name` / `version.code` | `2.0.2` / `202` |
| `plus.distribute.android.packagename` | `cc.cd.mono.xqmaster`（**必须和 MONO 那个不同**，否则两个 App 会互相覆盖） |
| `plus.distribute.google.appid`（Android 的 AppID） | 去 `dev.dcloud.net.cn` 新申请一个 |

> AppID 不能沿用 MONO 的，否则云端会认为是同一个应用。

### 4. 图标

把 `D:/MONO/MONO/icons/` 整个复制过来（`ldpi / mdpi / hdpi / xhdpi / xxhdpi`）。
想换成象棋主题的图标，替换这几个 PNG 即可，**尺寸别动**。

> 打包只认 `plus.distribute.icons.android` 这一段；同段里还有个同名的
> `splashscreen.android`，**按行号改最稳**，别全局替换。

### 5. 打包

HBuilderX 里：右键项目刷新 → 发行 → 原生App-云打包 → Android + **云端证书** → 排队等结果。

免费额度每天 3 次。产物在 `unpackage/release/apk/`。

## 已知取舍

- **包体积**：套壳 WebView，体积很小，但离线完全用不了（首屏必须联网）。
- **登录**：沿用社区账号，没有独立的游戏账号体系。
- **返回键**：App 内返回键由 WebView 自己处理，会退到网站上一页而不是退出 App。
  想改成「再按一次退出」，需要在 HBuilderX 里加 `plus.key` 监听 —— 需要的话我来加。
- **如果要做真正的原生体验**（离线可玩、原生返回栈、推送），
  那就不是套壳能解决的了，得单独做一个前端工程。这是另一个量级的活。

## 备选：不打包也能「装」

网站本身已经支持 PWA。手机上用浏览器打开 `https://mono-craft.de5.net/game/xqmaster`，
菜单里选「添加到主屏幕」，会得到一个独立图标 + 全屏运行的入口，
和 APK 的差别主要是：没有独立包名、不能上应用商店、首次仍需联网。
