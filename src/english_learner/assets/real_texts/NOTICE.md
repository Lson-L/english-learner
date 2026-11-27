# 真实文章库来源与许可

本目录的 `corpus.json` 是**构建期种子**，运行期由 `seed_real_texts()` 写入
SQLite 的 `real_texts` 表；app 阅读时只读 SQLite。

## 当前内容（version = 2）

| id | 标题 | 等级 | 词数 | 来源 | 许可 |
|---|---|---|---|---|---|
| `original-urban-beekeepers` | The Quiet Work of Urban Beekeepers | B1 | 257 | Daily English original | CC0 |
| `original-cooling-city-streets` | How Cities Are Cooling Their Streets | B2 | 400 | Daily English original | CC0 |

- 两篇均为本项目原创、按 CC0 1.0 贡献到公有领域，可随仓库公开分发；
- 待补到每档 ≥2 篇；当前 A2 / C1 仍缺，供给不足时会自动回退 AI 文章；
- 历史版本曾收录 China Daily 的个人学习材料（`Personal use`），已从公开
  仓库的种子中移除。若你在本机自行添加此类内容，**仅限本机个人使用，
  不要公开分发内容包或数据库**。

## 收录规则

- 进入公开仓库的语料只收公有领域 / CC0 / CC BY / CC BY-SA；
- 明确标记 `Personal use` 的个人材料可以本地使用，但不要提交到公开仓库，
  也不要公开分发由此导出的内容包或数据库；
- 每篇必须在 `corpus.json` 中记录 `source`、`source_url`、`license`、
  `attribution`；
- 不抓取付费墙 / 需要登录的内容，不绕过站点限制。

## 候选来源（以本机实际可访问为准）

- Project Gutenberg / Lit2Go / Simple English Wikipedia：公有领域或 CC 授权，
  但境内网络常不可达，需要用户提供正文或可访问镜像；
- China Daily / 人民网英文 / CGTN / 新华网英文：可访问，但内容多为版权保留，
  仅适合作为本机个人学习材料，不要进入公开仓库；
- 欢迎提交原创或明确 CC0 / CC BY 授权的文章与题目。
