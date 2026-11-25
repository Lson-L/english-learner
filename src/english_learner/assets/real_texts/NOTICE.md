# 真实文章库来源与许可

本目录的 `corpus.json` 是**构建期种子**，运行期由 `seed_real_texts()` 写入
SQLite 的 `real_texts` 表；app 阅读时只读 SQLite。

## 当前内容（version = 1）

| id | 标题 | 等级 | 词数 | 来源 | 许可 |
|---|---|---|---|---|---|
| `chinadaily-cook-islands-deep-sea` | Cook Islands scientist joins historic deep-sea dive | B1 | 277 | China Daily | Personal use |
| `chinadaily-shenzhou-24-experiments` | Shenzhou mission to conduct 90 sci-tech space tests | B2 | 445 | China Daily | Personal use |

- 原文链接见 `corpus.json` 每条的 `source_url`；
- **仅限本机个人学习使用，不要公开分发内容包或数据库**；
- 待补到每档 ≥2 篇；当前 A2 / C1 仍缺。

## 收录规则

- 只收公有领域 / CC0 / CC BY / CC BY-SA，或明确标记 `Personal use` 的个人材料；
- `Personal use` 仅限本机个人使用，不要公开分发；
- 每篇必须在 `corpus.json` 中记录 `source`、`source_url`、`license`、
  `attribution`；
- 不抓取付费墙 / 需要登录的内容，不绕过站点限制。

## 候选来源（以本机实际可访问为准）

- China Daily：主站长文可访问，正文提取稳定（当前使用）；
- 人民网英文 `en.people.cn`：可访问，但多为短讯 / 图集；
- CGTN / 新华网英文 / 英文巴士：首页可访问，正文提取待逐篇验证；
- Project Gutenberg / Lit2Go / Simple English Wikipedia：境内网络常不可达，
  需用户提供正文或可访问镜像。
