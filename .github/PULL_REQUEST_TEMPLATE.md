# 改动说明

<!-- 一句话说清楚这个 PR 做了什么 -->

## 为什么

<!-- 解决什么问题。关联的 issue：fixes #123 -->

## 怎么验的

- [ ] `python -m compileall -q .`
- [ ] `python tests/smoke.py`
- [ ] 手动点过相关页面（列出哪些）

<!-- 涉及界面的改动请附截图 -->

## 有没有动数据库

- [ ] 没动
- [ ] 动了，已写幂等迁移（`CREATE TABLE IF NOT EXISTS` / `_add_column`），本地跑过

## 有没有动文案

- [ ] 没动
- [ ] 动了，`i18n/zh.json` 和 `i18n/en.json` 都加了 key

## 其他

<!-- 需要 reviewer 特别注意的地方、已知的取舍、后续计划 -->
