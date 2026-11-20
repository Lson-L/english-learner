# Daily English 历史归档

> 这里保存已完成里程碑的交付记录、验证记录与旧任务书，**默认不需要阅读**。
> 排查“以前为什么这么改 / 当时验证了什么”时再按标题检索。
> 当前状态与下一步见 [PROGRESS.md](PROGRESS.md)，按需阅读 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [TECH_DEBT.md](TECH_DEBT.md)。
> 早期外部验收报告 `VERIFICATION_M3.9~M3.11.md` 已从仓库移除，结论摘要保留在本文。

## 里程碑交付记录（原 PROGRESS 第 1 节后半）

M0 结束时，应用可以：启动 → 强制配置 AI → 真实请求测试 → 保存配置 →
进入带 5 个页面的主窗口。

M1 结束时，应用可以走完**首次使用与每日阅读闭环**：
启动 → 配置 AI → 摸底测评（或手动选级）→ 首页显示等级/词汇量/连续天数 →
阅读页生成今日文章（落库缓存）→ 双击单词看语境释义（查词缓存）→
加入生词本并永久高亮 → 重启后文章与高亮仍在。生词本列表与 SM-2 复习留到 M2。

M1.5 追加：阅读页「调整难度」按钮。用户写一句阅读感受（或点快捷选项），
AI 结合当前等级、今日文章和今日生词重新定级，并按新等级 + 反馈重写今日文章；
今日生词会尽量复用，但流畅优先、不强制。旧文章的高亮会被清除，生词本保留。

M2 结束时，产品闭环第一次完整可走：
今日页显示「到期 N 词」→ 点「开始复习」进入卡片复习（空格翻面、1~4 评分）→
复习记录落 `review_logs`、`daily_stats.reviews_done` +1、打卡更新；
生词本页支持全部/未复习/学习中/已掌握筛选、关键词搜索、修改中英文释义、删除；
复习完全本地化，断网 / AI 不可用也能正常进行。首页「今日待复习」改为按
`due_date <= 今天` 统计。

M3 核心项完成（2026-11-19）：

- **数据导出**：设置页可导出 JSON（完整快照）或 CSV（当时七张表各一个文件，
  `utf-8-sig`，Excel 直接打开；M3.8 起为八张表）；导出在 TaskWorker 里执行；
- **API Key 加密**：Windows DPAPI（与当前用户绑定），旧明文配置可直接读取；
- **请求可取消**：`TaskWorker` 增加协作式取消与 `cancelled` 信号，
  关闭窗口时先取消并等待，避免 QThread 销毁警告；
- **错误分类**：`AIError` 增加 `kind / retryable / is_offline`，
  网络、超时、429、5xx 标记可重试；界面补充“复习离线可用”的提示；
- **生词本增强**：排序（最新/最早/字母/到期日）、批量删除、
  未保存释义的脏检查（切页、切筛选、关闭窗口都会提示）；
- **性能**：大列表刷新时暂停表格重绘、复习队列上限 200、导出不阻塞 UI。

深色模式与安装器 / 自动更新列为可选 M4，尚未实现。

M3.5 完成（2026-11-19）：

- **生词按原形入库**：查词时由 AI 判断词典原形（went→go），`words.lemma` 存原形，
  `words.display_form` 保留文章中的形态；新增 `word_forms` 词形缓存，
  同一词形第二次查询直接命中、不再调用 AI；
- **AI 记忆方法**：查词时一并生成 `mnemonic` 写入词库，阅读释义卡、生词本详情、
  复习答案页都会展示；老词条可在生词本详情点「AI 生成记忆方法」补生成；
- **复习求助**：复习时点「忘记 / 模糊」会弹出原因框（快捷原因 + 自由文本），
  AI 结合原因给出针对性建议并更新记忆方法；用户也可跳过 AI 直接记录评分，
  离线时复习不受影响；原因与建议写入 `review_logs.note / advice`；
- **数据库升级**：schema v1 → v2（`words.mnemonic`、`review_logs.note/advice`、
  `word_forms` 表），原地 `ALTER TABLE`，旧数据保留；
- **换一篇带上下文**：用户在「换一篇」里写了提示词时，请求会附带当前文章
  （标题、等级、主题、正文），便于理解“这个话题 / 这种结构”等指代；
  没写提示词时不带原文。

M3.6 完成（2026-11-19）：

- **阅读器布局**：移除右侧固定释义栏，正文占满整页；
- **悬浮释义卡**：双击单词后在词旁浮出 380px 卡片（带阴影、可滚动、带关闭按钮），
  点击正文 / 滚动 / 按 Esc / 切页都会收起；卡片始终夹在页面可视范围内。

M3.7 完成（2026-11-19）：

- **查词不再等于加入生词本**：`words` 新增 `in_vocabulary` 标记；双击只写查词缓存，
  必须点「加入生词本」才进入生词本、复习队列和首页统计；
- **生词本右侧排版**：详情内容放进滚动区，字段多或窗口矮时不再重叠/挤压；
- 数据库 schema v2 → v3，升级前的旧词统一保留为“已加入”（无法区分历史行为，
  宁可保留也不误删），新查词默认只进缓存。

M3.8 完成（2026-11-19）：

- **阅读完成入口**：阅读页标题栏新增「阅读完成」主按钮；已有当天检测结果时
  自动变为「查看答题结果」；
- **AI 出题**：按当天文章出 5 道单选题（主旨 / 细节 / 推断 / 语境词义），
  题目与解析落库缓存，同一篇文章只出一次题，重开不重复消耗 token；
- **答题与讲评**：弹窗内逐题选择后提交；客观题本地判分，AI 返回逐题中文解析、
  总评与新等级建议；结果页展示得分、正确率、等级变化和每题解析；
- **大屏答题**：弹窗带最大化按钮，并支持 F11 全屏 / Esc 退出全屏，
  题目与解析区随窗口自动铺满；
- **动态调级**：每次最多调整一档；全对至少升一档、答对不到一半至少降一档，
  其余尊重 AI 建议；调级结果写回 `profile`；
- **失败兜底**：出题/讲评失败都可重试，题目缓存保留；讲评失败时画像与题目
  状态都不改，不会出现“等级变了但没有结果”；
- **数据库升级**：schema v3 → v4，新增 `reading_quizzes` 表
  （`article_id` 唯一，文章替换时级联删除）；导出与 CSV 同步包含该表。

M3.9 第一阶段完成（2026-11-19）——让阅读检测更像“测评”，而不是“五道对错题”：

- **混合难度出题**：5 道选择题按“2 题当前等级 + 1 题低一档 + 1 题高一档 +
  1 题高两档”生成，每题带 `skill`（主旨/细节/推断/词汇/语法）和
  `difficulty`（A2/B1/B2/C1）；高难度题用来探上限，低难度题排查粗心；
- **英文短答**：额外 1 道短答题，要求写 1~2 句；选择题本地判分，短答由 AI
  给 0/1 分，补充“主动产出”信号；
- **错因归因**：AI 对每题返回 `error_types`
  （vocabulary / inference / detail / main_idea / grammar / careless / unknown），
  结果页对错题展示“错因”；
- **用户自评**：提交前可选择“这篇读起来：轻松 / 刚好 / 吃力”，作为弱证据；
- **本地证据评分**：按题目难度加权（答对高难度加分更多、答错低难度扣分更多），
  形成 -1~+1 的证据强度；AI 建议与本地证据同向才走，强证据可压过矛盾建议；
- **连续证据策略**：升级需要连续 2 次向上证据，第一次只记录
  `pending_level / pending_count`；降级立即生效；结果页明确显示
  “保持 B1 · 建议升到 B2（1/2）”；
- **数据库升级**：schema v4 → v5（`profile.pending_level/pending_count`；
  `reading_quizzes` 增加短答、错因、自评、建议等级、待定状态、证据强度）；
- **导出**：JSON / CSV 同步包含新增字段。

M3.10 完成（2026-11-19）——生词本回显复习求助：

- 复习时点「忘记 / 模糊」，在弹窗里写的原因（`review_logs.note`）和点
  「请 AI 帮我记」得到的建议（`review_logs.advice`）此前只落库、界面看不到；
- 生词本右侧详情新增「复习求助记录」区块，按时间倒序展示最近 5 条，
  每条带时间、评分档位（忘记 / 模糊）、「我的输入」和「AI 帮记」；
- 没有原因/建议的普通评分记录（认识 / 太简单）不展示；超过 5 条时提示
  “更早的 N 次求助已省略”；
- 数据仍只存 `review_logs`，不新增表/列，导出行为不变；
- 外部验收：两轮 GLM-5，**复用同一 session**，最终无 P0/P1/P2；详见
  `VERIFICATION_M3.10.md`（已移除）。

M3.11（Phase 2 / Phase 3）完成（2026-11-19）——把“单次检测调级”升级为“长期、可置信的水平估计”：

**Phase 2：本地证据模型 + 置信度**

- **等级证据表**：新增 `level_evidence`（题目级观测 `observations_json`、后验、
  置信度、错因、耗时等）；文章替换/检测被级联删除时证据仍保留（`quiz_id` 置空）；
- **本地后验**：`services/level_estimator.py` 用逻辑斯蒂模型
  `P(correct | L, d) = 1/(1+exp(k*(index(d)-index(L))))` 计算 A2/B1/B2/C1 后验；
  先验锚定当前等级，历史证据按 0.75 衰减回放最近 5 次；
- **置信度与决策**：只在“当前 ±1 档”窗口内取后验峰值；置信度 < 0.6 时保持当前等级；
  升级仍需连续两次向上证据，降级立即生效；
- **弱证据**：AI 盲评等级（权重 0.5）、自评（0.25）、按 CEFR 词档聚合的复习正确率
  （最多 0.8）都只作为弱观测；查词时 AI 顺带标注 `words.cefr_band`；
- **历史趋势**：讲评 prompt 附带最近 8 次检测摘要与难度/技能正确率；今日页显示
  近 4 周等级记录；结果页显示「B1（置信度 0.62）」与四档后验分布。

**Phase 3：自适应出题与更强诊断**

- **两阶段自适应**：第一轮 3 道当前等级题；答对 ≥ 2 道往上一档、否则往下一档，
  再出 2 道边界选择题 + 2 道产出题（多一次出题调用）；
- **题目审核**：每次出题后增加审核 pass，明确不合格的题丢弃并重出；
- **更强产出信号**：短答 + 句子改写/翻译各 1 道，评分固定「内容 / 语法 / 词汇」；
- **盲评与防作弊**：等级评估与讲评分两次调用，盲评 prompt 不含当前等级；
  选项顺序随机打乱并重映射正确答案；同一天只允许一次正式检测，
  换文章后的第二次检测自动进入练习模式（不写画像、不写证据）；
- **每周复盘**：今日页新增「本周复盘」，AI 汇总检测/生词/复习数据，
  只给建议；用户点「应用建议等级」后才写回画像，同一周结果缓存；
- **数据库升级**：schema v5 → v6（profile 置信度/原因；words.cefr_band；
  reading_quizzes 多产出题/后验/练习模式/阶段；新增 level_evidence、weekly_reviews）；
- **测试**：273 passed（新增估计器、自适应、盲评、练习模式、每周复盘、v5→v6 迁移等）；
- **外部验收**：见 `VERIFICATION_M3.11.md`（已移除）。

M3.12（AI 调用层与学习正确性修复）完成（2026-11-19）：

- **调用层稳定性**：`AIClient` 复用底层连接池并在退出时关闭；429/超时/5xx
  自动退避重试 1~2 次；所有业务调用显式设置 `max_tokens`；
  `finish_reason=length` 与空内容翻译成明确的中文错误；记录 token 用量与耗时；
- **JSON 能力缓存**：探测到网关不支持 `response_format` 后按 `(base_url, model)`
  记住，后续直接走普通模式，不再每次先发一次失败请求；
- **检测延迟**：正式检测的盲评与讲评并行执行；练习模式跳过盲评（其结果本就不参与决策）；
- **难度标签可信度**：第一轮难度本地强制回写为当前等级；第二轮选择题强制为边界等级，
  产出题收敛到“当前~边界”区间，模型自报 difficulty 不再直接污染 Rasch 后验；
- **答案键校验**：题目审核改为“审核模型独立作答 + 本地比对标注答案”，
  比只问“答案是否唯一”更容易发现答案键标错；
- **产出题漏评分**：AI 漏给 `production_scores` 时带原因重试一次；仍缺失则标记
  `scored=False`，不进入等级证据/历史统计，界面显示“AI 未评分”，不再静默算 0 分；
- **调级一致性**：阅读反馈调级增加 ±1 档 clamp 与方向兜底（说“太难”不允许升级，
  说“太简单”不允许降级），避免 C1 一次被打到 A2；
- **生成端闭环**：最近正确率低的技能进入出题 prompt；画像薄弱点作为软提示进入文章 prompt；
  文章/出题重试时把上次校验失败原因喂回模型；目标词校验接受常见词形变化；
- **查词语境**：命中缓存但句子不同时提示“这条释义来自另一句”，并提供「按本句重查」，
  避免多义词一直显示旧语境义；
- **清理**：文章 prompt 不再要求未被使用的 `glossary`；移除设置页中实际不生效的温度控件；
- **测试**：283 passed；外部验收见后续 `VERIFICATION_M3.12.md`。

M3.13（网关兼容）完成（2026-11-19）：

- **opencode 网关**：`base_url` 域名命中 `opencode.ai` 时，`AIClient` 自动附加
  `x-opencode-session` 头（每个客户端实例生成一个 UUID 并复用），否则网关一律
  返回 400 `MissingSessionID`；其它服务商不附加，避免污染通用配置；
- **OpenAI 兼容地址**：opencode 的 OpenAI 兼容入口是
  `https://opencode.ai/inference/go/openai/v1`（`/inference/go/anthropic` 是
  Anthropic Messages 协议，本项目用 OpenAI SDK，请求 `/chat/completions` 会 404）；
- **404 提示**：错误文案补充“本项目只支持 OpenAI 协议，路径含 `/anthropic`
  需换成 OpenAI 兼容地址”，不再只提示补 `/v1`；
- **测试**：新增会话头、域名匹配、非 opencode 地址不附加等用例。

M3.14（测评答题体验）完成（2026-11-19）：

- **“不知道”选项**：每道选择题在 A~D 后追加「E. 不知道」，用户不用再靠瞎猜；
  新增作答下标 `UNKNOWN_ANSWER = -2`，与 `-1`（未作答）区分；本地计分时
  “不知道”按答错处理，但 Prompt 里会明确标注，便于 AI 区分“真不会”和“跳过”；
- **每题补充说明**：选择题选项下方增加一行选填输入框（例如“题目里的单词不认识”），
  按题目下标随 `AnswerSheet.comprehension_notes / vocabulary_notes` 一起交给 AI；
  只作为判断薄弱点、区分“词汇不认识”与“题意没读懂”的参考，不参与本地计分；
- **选择题必答**：提交前统计 `checkedId() == -1` 的题数，有未作答时提示
  “不会就选「不知道」”并阻止提交；翻译题仍可留空；
- **Prompt**：`_format_questions` 输出“用户答案：不知道 / 未作答”和
  “用户补充：…”，`PROMPT_VERSION` 升到 `m3-14`；
- **测试**：288 passed（新增不知道计分、Prompt 标注、空答与 GUI 必答拦截等用例）。

M3.15（AI 调用记录与日志查看）完成（2026-11-19）：

- **调用记录落库**：新增 `ai_calls` 表（schema v6 → v7）。`AIClient` 在每次真实
  HTTP 尝试后记录：时间、任务名、模型、base_url、JSON 模式、第几次尝试、
  输入/输出/总 token、耗时、成功与否、错误分类与错误摘要；不保存提示词与模型回复。
  重试和 `response_format` 降级会各占一行，token 统计按真实请求数计费；
- **任务标注**：`complete / complete_json` 新增 `task` 参数（默认空），
  测评、每日文章、查词、记忆方法、阅读反馈调级、阅读检测（第一轮/第二轮出题、
  题目审核、讲评、盲评）、复习求助、每周复盘、连接测试都传入稳定任务名；
- **记录不阻断业务**：记录通过 `AICallRecorder` 协议注入（`AppContext.client()`
  自动接 `db.ai_calls`），仓库抛异常只写 debug 日志，不影响 AI 结果；
  网关不返回 `usage` 时 token 记为 NULL，统计时被 SUM 忽略、界面显示 “—”；
- **设置页**：新增「AI 使用与日志」卡片，显示今日/累计调用次数与 token，
  入口「查看调用记录…」「查看运行日志…」；切回设置页自动刷新摘要；
- **调用记录弹窗**：今天 / 近 7 天 / 近 30 天 / 全部；调用次数、失败数、
  输入/输出/总 token、平均耗时；按任务聚合（哪类操作最费 token）；
  最近 500 条明细，可按任务筛选、只看失败、悬停看错误详情；支持清空记录；
- **日志查看器**：应用内读取 `logs/app.log` 与轮转备份（最新在前），
  只显示末尾 256 KB，支持关键字逐行筛选、手动刷新与 3 秒自动刷新；
  「打开日志目录」保留系统文件管理器入口；实现放在 `services/log_files.py`（可单测）；
- **导出同步**：JSON 快照与 CSV 分表增加 `ai_calls`（第十一张表）；
- **布局修正**：汇总区改为单行 6 个指标（22px），内容放进滚动区；
  窗口压到最小高度时不再把汇总数字压成细条，表格改用收敛 sizeHint 的
  `_CompactTable`，正常高度仍可拉伸铺满；
- **测试**：314 passed（新增仓库汇总/筛选、客户端成功/失败/重试/截断/仓库异常、
  日志尾部读取与轮转排序、用量弹窗与日志查看器 offscreen、设置页摘要等用例），
  `ruff check` 与 `ruff format --check` 通过。

M3.16（复习拼写回忆）完成（2026-11-19）：

- **目标**：原来的复习只能“看词回忆”（再认），新增“拼写回忆”（主动产出），
  帮助用户真正记住拼写；默认仍保持看词回忆，模式可随时切换；
- **判分服务**：新增 `services/recall.py`，纯函数、不依赖 PySide6 / AI。
  归一化忽略大小写、首尾空白、连续空白与首尾标点；同时接受 `lemma` 与
  `display_form`（文中 went、原形 go 都算对）；编辑距离 1 判为“差一点”
  （仅 3 个字母以上启用），只给建议评分，不自动改 SM-2；
- **题面生成**：优先中文释义 + AI 例句，例句没出现目标词时退回文章原句；
  用单词边界把目标词替换成 `____`，避免把 go 挖进 good / going；
  释义里如果带了英文原词也会挖掉。短语（含空格）或缺少释义的词自动回退到
  看词回忆，不强行出拼写题；
- **复习页**：顶部新增「复习模式」下拉框（看词回忆 / 拼写回忆），选择写入
  `config.json` 的 `ui.review_mode`，会话进行中禁用切换。拼写正面只给释义、
  词性和挖空例句，原词 / 原形 / 音标 / 原文全部隐藏；输入英文后回车判分，
  显示正确 / 差一点 / 不对与标准答案，再出现原来的 1~4 评分栏，
  评分提示给出建议档位但最终由用户确认；
- **快捷键**：拼写输入阶段禁用全部 QShortcut，否则空格、回车和数字键会被
  快捷键抢走；判分翻面后恢复。跳过输入按钮直接看答案，不计作答、不给建议；
- **偏好兼容**：`UiConfig` 新增 `review_mode`（默认 `recognition`，非法值回退）；
  设置页改英文字体时改用 `dataclasses.replace`，不会冲掉复习模式；
- **测试**：341 passed（新增判分归一化/词形/编辑距离/挖空边界、拼写模式切换与
  持久化、正确/差一点/错误/跳过/短语回退/快捷键状态、UiConfig 回退与设置页
  保留偏好等用例），`ruff check` 与 `ruff format --check` 通过。

M3.17（阅读检测产出题改为英译中）完成（2026-11-19）：

- **产品目标**：阅读检测的输入题不再要求学习者用英文短答或改写，统一改成
  「从原文选一句英文 → 翻译成中文」，只检验阅读理解，不练英语写作；
- **出题**：第二轮仍出 2 道边界选择题，另出 2 道英译中题
  （`kind=short`、`skill=translation`）。第 1 道选关键信息/主旨句，
  第 2 道选含指代、转折、因果等逻辑关系的句子；题面要求直接引用原文，
  长度 12~35 个英文单词，`reference_answer` 必须是中文参考翻译；
- **校验**：`parse_stage_two_questions()` 强制产出题 `skill=translation`，
  并要求题面明确出现“中文 / Chinese”；模型退回英文短答/改写时判不合格并
  带原因重试，避免用户重新开始练写作；
- **讲评**：英译中题按「理解准确 / 中文通顺」评分：准确传达原句意思给 1；
  关键词义、指代、否定或逻辑关系译错/漏译/译反给 0；不要求逐字直译，
  不因中文文采扣分。旧缓存里的英文短答/改写仍按「内容 / 语法 / 词汇」评分；
- **兼容**：新增 `QuizQuestion.is_translation`（skill=translation 且题面要求
  译成中文），UI、讲评、盲评、错因统计共用这一个判断；旧缓存题目继续按
  原标签展示与评分，不需要迁移数据库；
- **技能统计**：英译中单独进入 `translation` 技能桶；薄弱技能提示会过滤
  `translation/writing/rewrite` 等产出题专用技能，避免让选择题去“覆盖”它们；
  错因统一记 `translation`，结果页显示「英译中理解偏差」；
- **UI**：第二轮标题、提示、输入框 placeholder、提交校验、结果卡片全部改为
  中文译文口径（“你的译文 / 参考翻译”）；旧题仍显示“短答 / 句子改写”；
- **摸底测评同步**：A2 套原来唯一一道“中译英”改成英译中（原文选句），
  测评评分说明也改为重点看理解准确度；全应用不再出现要求写英文的输入题；
- **测试**：349 passed（新增英译中出题/解析/拒绝英文产出、讲评 rubric、
  薄弱技能过滤、弹窗中英译中占位符与旧题兼容等用例），
  `ruff check` 与 `ruff format --check` 通过。

## 5. 验证记录

### 5.1 M0（2026-11-18）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **18 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format` | 已格式化 |
| offscreen 渲染 4 个界面（欢迎、配置、今日、设置） | 布局正常 |

### 5.2 M1（2026-11-18）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **91 passed**（M1 主体 62 + 字体 11 + 反馈调级及配套 18） |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 已格式化 |
| 临时 DB 连续初始化两次 | 通过（`user_version = 1`，表齐全） |
| offscreen GUI（主窗口 5 页、今日页读库、测评弹窗、反馈弹窗） | 通过 |
| 反馈调级：AI 改级 → 文章原子替换 → 高亮清除、生词保留 | 通过（mock） |
| 反馈调级：AI 失败时旧文章与旧画像不变 | 通过（mock） |

M1 仍未验证 / 需要人工确认：

1. **真实模型端到端**：需要真实 Key 跑完整闭环（测评 → 生成文章 → 查词 → 高亮
   → 阅读反馈调级 → 新文章）。
   代码路径已用 fake completer 覆盖，但真实网关的 JSON 稳定性、文章质量未测。
2. **`response_format` 兼容性**：`AIClient.complete_json` 在识别到
   “不支持 response_format” 时会自动去掉该参数重试一次；真实网关的报错文案
   是否都能被识别，需要实测（识别规则见 `ai_client._json_mode_unsupported`）。
3. **真实窗口排版 / 字体**：offscreen 没有字体库，字号/行距/高亮颜色与内置字体
   实际效果需在真实 Windows 桌面确认。
4. **文章词数**：本地校验放宽到 200~400，prompt 要求 250~350；不同模型可能
   频繁卡在边缘，需要看实际重试率。
5. **反馈调级效果**：AI 是否真的按“至少降一档”执行、新文章生词密度是否明显下降，
   需要真实模型实测；prompt 已把反馈传给写文章的模型，但仍依赖模型遵守。

> offscreen 模式下 Qt 字体库为空，截图里中文是方框，这是**离屏环境限制**，
> 不是应用 bug；真实 Windows 桌面会正常加载微软雅黑。

### 5.3 M2（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **122 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 54 files already formatted |
| SRS 纯函数 | 忘记重置、1/6 天起步、易度下限 1.3、间隔上限 365、掌握阈值 30 天 |
| 复习服务 | 到期过滤、调度落库、`review_logs`、`daily_stats.reviews_done` |
| 复习页 offscreen | 概览 / 翻面 / 评分 / 会话统计 / 无到期禁用开始 |
| 生词本页 offscreen | 筛选 / 搜索 / 编辑释义 / 删除 / 空状态 |

M2 仍未验证 / 需要人工确认：

1. **真实窗口排版与快捷键**：复习卡片的字号、按钮配色，数字键 1~4 在中文输入法
   或按钮焦点下是否稳定触发；
2. **调度节奏体感**：1 / 6 / 15 天……的间隔是否符合真实记忆曲线，是否需要
   调 `services/srs.py` 里的系数；
3. **生词本规模**：几百条词时 `QTableWidget` 的滚动与全量刷新性能；
4. **积压复习**：一轮上限 200 词，积压更多时「再检查一次到期词」的体验。

### 5.4 M3 核心项（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **156 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 60 files already formatted |
| DPAPI | 加密往返、同一明文两次密文不同、旧明文配置可直接读、损坏密文报 ConfigError |
| 导出 | JSON 快照字段/计数、CSV 六表/中文编码、空库、临时文件原子替换 |
| 取消 | 启动前取消、执行中取消丢弃结果、AIError/未知异常翻译、`stop()` 等待 |
| 生词本 | 四种排序、批量删除、脏检查保存/丢弃/取消、300 条列表刷新 |
| 设置页导出 | 按钮可用性、真实写文件、结果/失败/取消状态提示 |

M3 仍未验证 / 需要人工确认：

1. **DPAPI 的跨机器行为**：复制 `config.json` 到另一台电脑/用户后应提示重新配置，
   需在真实双机环境确认错误文案与恢复流程；
2. **关闭窗口的等待体验**：网络请求进行中关闭窗口时最长等待一次 HTTP 超时，
   需要真实弱网环境观察是否可接受；
3. **深色模式 / 安装器**：未实现（可选 M4）。

### 5.5 M3.5（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **179 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| schema v1 → v2 | 旧库原地升级、旧词条与复习记录保留、新列/`word_forms` 就位 |
| 原形与词形 | went→go 入库、display_form=Went、二次查 went 命中缓存不调 AI |
| 记忆方法 | 查词写入、老词单次生成、失败抛 AIError；释义卡/生词本/复习页展示 |
| 复习求助 | 建议解析、mnemonic 落库、无 advice 报错、跳过/取消不误记评分 |
| 复习日志 | `note / advice` 随评分落库并可导出 |
| 导出 | 当时七张表（新增 `word_forms`）与新增字段全部包含（M3.8 起为八表） |
| 换一篇上下文 | 有提示词时 prompt 含当前文章正文；无提示词时不含（含 builder 兜底） |

M3.5 仍未验证 / 需要人工确认：

1. **真实模型的原形判断质量**：went→go、better→good 等是否稳定；错判时目前没有
   “手动改原形”的入口（只能删词重查）；
2. **记忆方法质量**：联想/谐音是否真的容易记，需真实使用后调 prompt；
3. **复习求助的成本与节奏**：每点一次“忘记/模糊”就多一次 AI 调用，
   若觉得太频繁，可增加“本次复习不再提示”或按等级开关。

### 5.6 M3.6 阅读器悬浮卡（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **180 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| 布局 | 阅读页无 QSplitter，正文占满；释义卡是页面子控件，默认隐藏 |
| 定位 | 有取词矩形时贴在词下方，越界会翻到上方并夹在页面内 |
| 收起 | 点击正文、滚动正文、按 Esc、点关闭按钮、切文章/切页都会隐藏 |
| 内容 | 卡片宽度 380px，释义内容放滚动区，长内容不截断 |

M3.6 仍未验证 / 需要人工确认：

1. **真实窗口的视觉效果**：阴影、圆角、卡片与单词的相对位置需要在真实桌面看；
2. **offscreen 布局限制**：`cursorRect` 在无窗口的 offscreen 环境下可能尚未计算，
   因此相关单测只验证“记录了矩形 + 卡片夹取”，真实取词位置需人工确认。

### 5.7 M3.7 查词/生词本分离与排版（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **181 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| schema v2 → v3 | 真实库副本升级成功：13 个旧词全部保留并标记为已加入 |
| 查词缓存 | 新的 `lookup()` 写入 `in_vocabulary=0`，不出现在生词本/复习队列/统计 |
| 加入生词本 | `add_to_vocabulary()` 置 1；重复加入幂等，状态不回退 |
| 生词本排版 | 右侧详情放入 QScrollArea；offscreen 验证无重叠、无横向滚动 |
| 导出 | 导出仍包含全部缓存词（`include_cache=True`） |

M3.7 仍未验证 / 需要人工确认：

1. **真实窗口右侧排版**：滚动条外观、底部按钮位置需要真实桌面确认；
2. **历史自动词条**：升级前的词无法区分“查过”与“真正加入”，全部保留；
   如不需要可在生词本里批量删除。

### 5.8 M3.8 阅读检测（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **205 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 出题解析 | 支持 `answer_index` 数字 / “B” / “B. xxx”；不足 3 题判失败并重试 |
| 缓存 | 同一篇文章第二次 `get_or_create` 不再调用 AI |
| 判分 | 本地按下标判分；满分 5/5、3/5、0/5 均正确 |
| 调级约束 | 全对至少升一档、低分至少降一档、每次最多一档、非法等级回退 |
| 失败兜底 | 讲评 JSON 失败不改 profile、题目仍未完成，可直接重试 |
| 存储 | `reading_quizzes` 往返、唯一约束、文章删除级联清理 |
| 导出 | JSON 含题目数组与 total；CSV 第 8 张表 `reading_quizzes.csv` |
| GUI | 缓存题目直接展示、未答完拦截、提交后结果页、已完成直接看结果 |
| 全屏 | 最大化按钮存在；F11 进入全屏、再按 Esc 只退出全屏不关窗 |

M3.8 仍未验证 / 需要人工确认：

1. **真实模型的出题质量**：干扰项是否合理、答案是否唯一、解析是否准确，
   需要真实文章多次实测后调 prompt；
2. **调级体感**：全对必升一档是否过于激进、3/5 保持是否合适，
   建议连续使用一周后看等级变化曲线；
3. **真实窗口排版**：题目选项较长时的换行、结果页多题解析的滚动体验需要
   真实桌面确认；
4. **重复检测**：当前设计是一篇文章一套题、只能提交一次；如果用户中途关闭，
   重开会继续用同一套题（这是有意的缓存行为）。

### 5.9 M3.9 阅读检测第一阶段（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **212 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 混合难度 | 5 道选择题覆盖低一档/当前/高一档/高两档；解析缺失难度回退本卷等级 |
| 短答 | 必须写 1~2 句；空短答强制 0 分；AI 给 0/1 分 |
| 错因 | 每题 error_types 与题目对齐；答对强制 none，短答固定 short_answer |
| 证据评分 | 高难度答对权重更高；本地 ratio 与 AI 建议冲突时强证据可压过 AI |
| 连续调级 | 第一次向上只记 pending(1/2)；第二次才升级；降级立即生效 |
| 存储 | schema v5 迁移、profile pending 字段、quiz 新字段往返 |
| 导出 | JSON / CSV 包含短答、错因、自评、建议等级、待定状态、证据强度 |
| 外部验收 | 两轮 GLM-5 复验：第 1 轮发现 1 个 P1 + 2 个 P2，已修复；第 2 轮通过。详见 `VERIFICATION_M3.9.md`（已移除） |

M3.9 仍未验证 / 需要人工确认：

1. **真实模型出题质量**：混合难度的配比是否被模型遵守、短答评分是否稳定；
2. **等级变化体感**：连续两次升级是否太慢/太快，需要连续使用一周观察；
3. **真实窗口排版**：短答输入框、自评下拉、结果页长解析在全屏/小窗口下的体验；
4. **降级策略**：目前降级不等待连续证据，是否会在疲劳答题时误降，需要实测。

### 5.10 M3.10 生词本回显复习求助（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **218 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 有求助记录 | 详情显示时间、评分档位、用户输入、AI 帮记内容 |
| 无求助记录 | 显示引导文案；「认识 / 太简单」的空记录不展示 |
| 多条记录 | 最新在前，最多 5 条；更多时提示更早 N 次已省略 |
| 数据层 | 复用 `review_logs.note / advice`，无 schema 变更、导出行为不变 |
| 仓库层 | `list_help_for_word / count_help_for_word` 的过滤、倒序、并列时间、limit 边界有直接单测 |
| 外部验收 | 两轮 GLM-5 **同一 session**：第 1 轮 13 项通过 + 2 个 P3；第 2 轮确认无 P0/P1/P2。详见 `VERIFICATION_M3.10.md`（已移除） |

M3.10 仍未验证 / 需要人工确认：

1. **真实窗口排版**：右侧 360px 面板里长建议的换行与滚动体验需真实桌面确认；
2. **记录时效**：本次求助只有在点「完成」记录评分后才落库；如果用户生成
   AI 建议后直接关窗取消评分，该条建议不会出现在生词本（符合现有评分语义）。

### 5.11 M3.11 Phase 2 / Phase 3（2026-11-19）

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **273 passed** |
| `uv run ruff check` / `ruff format --check` | 通过 / 通过 |
| 本地后验 | 逻辑斯蒂模型 + 先验 + 历史衰减；全对/全错/混合/缺失难度/边界等级单测；简单题不升级、高难题升一档场景回放 |
| 自适应出题 | 3 道当前等级 → 按 2/3 正确率选边界 → 2 选择题 + 2 产出题；第一轮答案先落库、重开回显 |
| 盲评 | 独立调用、prompt 不含当前等级与难度；高于当前 → 正向证据，低于当前 → 负向证据；失败不阻塞 |
| 反作弊 | 选项打乱 + answer_index 重映射；同一天第二次检测进入练习模式，不写画像/证据/历史统计 |
| 每周复盘 | 同一周缓存；AI 只建议；最多一档、置信度 < 0.6 保持；用户确认后才写 profile |
| 迁移 | v5 → v6 原地迁移；旧检测不回填、stage 默认 2、可继续提交；新增 `level_evidence` / `weekly_reviews` |
| 导出 | JSON / CSV 十张表，含两张新表与全部新列 |
| 外部验收 | 4 轮静态审查（同一 session + 新 session）：P2 盲评方向 + 多个 P3 已修复，无 P0/P1/P2；外部 CLI 权限层始终不放行 pytest/ruff，动态检查以开发会话自检 273 passed 归档。详见 `VERIFICATION_M3.11.md`（已移除） |

M3.11 仍未验证 / 需要人工确认：

1. **真实模型出题/审核质量**：自适应边界题、改写题和审核 pass 的实际稳定性；
2. **后验阈值体感**：斜率 1.1、先验 0.4、置信度 0.6、历史衰减 0.75 仍是经验值；
3. **真实窗口排版**：两轮答题、长解析、产出题输入框和每周复盘弹窗需真实桌面确认；
4. **AI 调用成本**：一次完整检测最多 4 次调用（两轮出题 + 盲评 + 讲评）+ 审核，
   真实网关较慢时需要观察体感。

### 5.12 M3.15 AI 调用记录与日志查看（2026-11-19）

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **314 passed** |
| `uv run ruff check` / `ruff format --check` | 通过 / 81 files formatted |
| 迁移 | v6 → v7 新增 `ai_calls` 表与时间/任务索引；旧库启动自动补齐，不影响原有数据 |
| 埋点 | 成功、401、429 重试两次、截断、网关不返回 usage、记录仓库抛异常均有单测；记录失败不改变 AI 返回 |
| 统计口径 | 每次真实 HTTP 尝试一行；失败/降级各自记录；`attempt` 连续；NULL tokens 不计入 SUM |
| 界面 | 设置页今日/累计摘要；用量弹窗范围/任务/只看失败筛选、清空确认；日志查看器筛选与自动刷新（offscreen） |
| 日志读取 | 尾部 256 KB、半行丢弃、无效 UTF-8 容错、轮转文件按修改时间倒序 |
| 导出 | JSON/CSV 十一张表，`ai_calls` 字段可回放 |
| 未验证 | 真实网关的 `usage` 覆盖度、真实桌面下两个新弹窗的排版、日志文件很大时的滚动体验 |

## 7. M1 详细任务（已全部完成）

> M1 目标：跑通「测评定级 → 生成今日文章 → 阅读划词 → 生词入本」，
> 复习留到 M2。每个任务完成即补测试，不要攒到最后。
>
> **状态（2026-11-18）：M1-1 ~ M1-7 全部完成**，验证见 5.2。
> 实际实现与原计划的差异（以实际代码为准）：
>
> 1. 新增 `services/app_context.py`（AppContext 门面）与 `services/ai_protocol.py`
>    （`TextCompleter` 协议），页面/服务只依赖这两个抽象；
> 2. `AnswerSheet` 用选项下标元组（`comprehension` / `vocabulary`），未引入题目 id；
> 3. 查词返回 `LookupResult(word, cached)` 而不是裸 `WordDefinition`，
>    因为加入高亮需要 `word.id`；`WordDefinition.from_word()` 仍保留；
> 4. `AIClient.complete_json()` 在服务端疑似不支持 `response_format` 时
>    自动去掉该参数重试一次，业务层再叠加本地三档 JSON 解析；
> 5. 测评默认固定 B1 套（`DEFAULT_SET_KEY = "b"`），A2/B2 套已写好但 UI 暂不选择；
> 6. “今日待复习”按 M1 约定显示未掌握词数（`WordRepo.count_unmastered`），
>    `StatsRepo.count_due` 已实现并测试，M2 首页直接切；
> 7. 阅读页把“生成/重试”做成标题栏按钮；进入页面且当天无文章时自动生成。

### M1-1 存储层（建议先做）

新增：

```
src/english_learner/models/__init__.py
src/english_learner/models/entities.py      # 领域 dataclass + from_row
src/english_learner/storage/__init__.py
src/english_learner/storage/db.py           # 连接、建表、迁移
src/english_learner/storage/repositories.py # 纯 SQL 仓库
```

- DB 路径：`config_dir() / "data.db"`，构造函数允许传入临时路径供测试。
- 连接：`row_factory=sqlite3.Row`、`PRAGMA foreign_keys=ON`、
  `PRAGMA journal_mode=WAL`。
- 迁移：`PRAGMA user_version`，当前 `SCHEMA_VERSION = 1`；
  初始化可重复执行（`CREATE TABLE IF NOT EXISTS`）。

建议 schema（M1 一次建全，M2 不再迁移）：

```sql
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cefr_level      TEXT    NOT NULL,
    vocab_estimate  INTEGER NOT NULL DEFAULT 0,
    strengths_json  TEXT    NOT NULL DEFAULT '[]',
    weaknesses_json TEXT    NOT NULL DEFAULT '[]',
    comment         TEXT    NOT NULL DEFAULT '',
    assessed_at     TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    article_date     TEXT    NOT NULL UNIQUE,   -- YYYY-MM-DD，一天一篇
    title            TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    level            TEXT    NOT NULL,
    topic            TEXT    NOT NULL DEFAULT '',
    word_count       INTEGER NOT NULL DEFAULT 0,
    target_words_json TEXT   NOT NULL DEFAULT '[]',
    read_at          TEXT,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma           TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    display_form    TEXT    NOT NULL DEFAULT '',
    phonetic        TEXT    NOT NULL DEFAULT '',
    pos             TEXT    NOT NULL DEFAULT '',
    meaning_cn      TEXT    NOT NULL DEFAULT '',
    meaning_en      TEXT    NOT NULL DEFAULT '',
    example_en      TEXT    NOT NULL DEFAULT '',
    example_cn      TEXT    NOT NULL DEFAULT '',
    context_sentence TEXT   NOT NULL DEFAULT '',
    article_id      INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    status          TEXT    NOT NULL DEFAULT 'new',  -- new/learning/mastered
    ease            REAL    NOT NULL DEFAULT 2.5,
    interval_days   INTEGER NOT NULL DEFAULT 0,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS highlights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    word_id      INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset   INTEGER NOT NULL,
    sentence     TEXT    NOT NULL DEFAULT '',
    UNIQUE (article_id, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS review_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    rating        INTEGER NOT NULL,          -- 1/2/3/4
    prev_interval INTEGER NOT NULL,
    next_interval INTEGER NOT NULL,
    reviewed_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date    TEXT PRIMARY KEY,           -- YYYY-MM-DD
    article_read INTEGER NOT NULL DEFAULT 0,
    words_added  INTEGER NOT NULL DEFAULT 0,
    reviews_done INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_words_due ON words(due_date);
CREATE INDEX IF NOT EXISTS idx_highlights_article ON highlights(article_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_word ON review_logs(word_id);
```

仓库方法（建议最小集）：

- `ProfileRepo.get() / save(profile)`
- `ArticleRepo.get_by_date(date) / insert(article) / delete_by_date(date) / list_recent(limit)`
- `WordRepo.get_by_lemma(lemma) / upsert(word) / add_highlight(...) / list_highlights(article_id) / list_all(status=None) / count()`
- `StatsRepo.bump(date, article_read=0, words_added=0, reviews_done=0) / get(date) / count_due(today)`

验收：临时 DB 连续初始化两次无异常；CRUD 往返一致；同一天重复插文章触发唯一约束；
同一个 lemma 大小写不同视为同一词。

### M1-2 领域模型

`models/entities.py` 用 frozen dataclass：

- `Profile(cefr_level, vocab_estimate, strengths, weaknesses, comment, assessed_at)`
- `Article(id, article_date, title, content, level, topic, word_count, target_words, read_at, created_at)`
- `Word(...)` 对应 words 表
- `Highlight(article_id, word_id, start_offset, end_offset, sentence)`
- `WordDefinition(lemma, display_form, phonetic, pos, meaning_cn, meaning_en, example_en, example_cn)`
- `AssessmentResult(cefr, vocab_estimate, strengths, weaknesses, comment)`

每个类提供 `from_row(row)`；写 `to_dict()` 只在需要时加，别过度设计。

### M1-3 摸底测评

新增：

```
src/english_learner/services/assessment_bank.py   # 3 套固定题
src/english_learner/services/assessment_service.py
src/english_learner/services/json_utils.py        # parse_json_object 兜底
src/english_learner/gui/assessment_dialog.py
```

题库结构（纯 Python dataclass，便于类型检查和测试）：

```python
@dataclass(frozen=True)
class Question:
    prompt: str
    options: tuple[str, ...]
    answer_index: int

@dataclass(frozen=True)
class AssessmentSet:
    key: str                 # "a" / "b" / "c"
    level_hint: str          # "A2" / "B1" / "B2"
    passage: str             # 150~180 词
    comprehension: tuple[Question, ...]   # 5 题
    vocabulary: tuple[Question, ...]      # 5 题
    translation_prompt: str               # 1 段中译英或英译中
```

流程：

1. 首次测评固定用 B1 套（`level_hint="B1"`），不要一开始就自适应，先保证可复现；
2. 用户作答（选择题 + 翻译文本）；
3. 本地先算出客观题得分，把题目、标准答案、用户答案、翻译一起交给 AI，
   要求只输出评分 JSON（AI 不重新出题）；
4. 结果落库 `profile`；
5. AI 评分失败时，界面允许手动从 A2/B1/B2/C1 选一个等级，**不能卡死流程**。

评分 JSON 契约：

```json
{
  "cefr": "B1",
  "vocab_estimate": 3800,
  "strengths": ["阅读理解", "基础语法"],
  "weaknesses": ["学术词汇", "长难句"],
  "comment": "整体 B1 偏上，建议先扩大阅读量。"
}
```

解析要点：`parse_json_object` 先原样 `json.loads`，失败则去掉 ```json 围栏，
再失败则截取第一个 `{` 到最后一个 `}`。字段缺失时给默认值，`cefr` 必须在
A2/B1/B2/C1 中，否则回退到 `level_hint`。

验收：mock AI 返回合法 JSON → profile 入库；返回带围栏的 JSON → 仍能解析；
返回垃圾 → 抛 AIError，界面出现手动选级入口。

### M1-4 每日文章生成

新增：

```
src/english_learner/services/prompts.py          # 所有 prompt 集中在这里，带版本号
src/english_learner/services/article_service.py
```

接口建议：

```python
def get_or_create_today(profile: Profile, due_words: list[str]) -> Article
def generate_article(level: str, target_words: list[str], topic: str) -> ArticleDraft
def regenerate_today() -> Article
```

生成契约（JSON）：

```json
{
  "title": "The Quiet Power of Small Habits",
  "level": "B1",
  "topic": "psychology",
  "paragraphs": ["...", "...", "..."],
  "target_words": ["resilient", "consistent"],
  "glossary": [{"word": "resilient", "meaning_cn": "有韧性的"}]
}
```

本地校验（不合格就重试一次，仍失败抛 AIError）：

- `title` 非空，`level` 等于请求等级（允许大小写差异）；
- `paragraphs` ≥ 3，合并后词数在 200~400（目标 250~350）；
- `target_words` 里的每个词都出现在正文中（忽略大小写）；
- 段落不能是空串；去掉首尾空白后重新拼接 `content = "\n\n".join(paragraphs)`；
- `word_count = len(content.split())`。

缓存策略：`articles.article_date` 唯一；当天已有则直接返回。
“换一篇” = 删除当天记录后重新生成（UI 需二次确认，避免误删已标生词的高亮）。
注意：删除文章会级联删除 highlights，但 words 保留（生词本不依赖某篇文章）。

UI 触发：进入「今日」或「阅读」页时，若今天没有文章，后台生成并显示
“正在生成今日文章…”；失败显示重试按钮。生成中禁用“换一篇”。

Prompt 要点：

- 明确 CEFR 等级、词数、段落数、主题；
- 要求“自然复现 target_words，每个最多出现 2 次，不要堆砌”；
- 要求只输出 JSON，不加解释；
- 把 prompt 模板和版本号写进 `prompts.py`，便于以后对比效果。

### M1-5 阅读器与划词释义

新增 / 修改：

```
src/english_learner/gui/pages/reader.py          # 替换占位
src/english_learner/gui/widgets/__init__.py
src/english_learner/gui/widgets/word_panel.py    # 释义卡（M3.6 起为悬浮卡）
src/english_learner/gui/widgets/article_view.py  # QTextEdit 子类（双击取词）
src/english_learner/services/dictionary_service.py
```

阅读器实现建议：

- 用只读 `QTextEdit` + `setPlainText(article.content)`；
- 双击：`cursorForPosition(event.pos())` → `QTextCursor.WordUnderCursor`
  → 取选中文本、`selectionStart/End`、所在句子；
- 高亮：`QTextEdit.ExtraSelection` + `QTextCharFormat.setBackground(...)`，
  从 `highlights` 表读取偏移量；加入生词后立即重绘；
- 字号 / 行距用 `QTextBlockFormat` 设置（M1 可先固定 17px / 1.8 倍）；
- 句子边界：以 `.!?` 为界，向前后各找最近的分隔符，限制长度 300 字符。

查词服务：

```python
def lookup(word: str, sentence: str, article_id: int, level: str) -> WordDefinition
```

1. 规范化 lemma：`strip().lower()`，去掉首尾标点；
2. 先查 `words` 表，命中直接返回（不花 token）；
3. 未命中调用 AI，要求“结合上下文给出语境义”，JSON 契约：

```json
{
  "lemma": "resilient",
  "phonetic": "/rɪˈzɪliənt/",
  "pos": "adj.",
  "meaning_cn": "有韧性的；能迅速恢复的",
  "meaning_en": "able to recover quickly from difficulties",
  "example_en": "Children are often more resilient than adults expect.",
  "example_cn": "孩子往往比大人想象的更有韧性。"
}
```

4. 结果写 `words`，`status='new'`，`due_date=today`（M2 复习从今天开始）。

加入生词本：

- `WordRepo.upsert()` 以 lemma 唯一；
- 插入 `highlights(article_id, word_id, start_offset, end_offset, sentence)`；
- `daily_stats.words_added += 1`；
- 重复添加同一位置要幂等（UNIQUE 约束 + `INSERT OR IGNORE`）。

验收（mock AI + 临时 DB）：

- 双击单词 → 加载中 → 释义卡出现；
- 加入生词本 → 正文高亮，重启应用后高亮仍在；
- 同一个词第二次查不调用 AI；
- 标点、数字、超长选中不崩溃（不查或提示“请选择一个英文单词”）。

### M1-6 首页接真实数据

- `TodayPage` 注入 ProfileRepo / ArticleRepo / StatsRepo（或一个 `AppState` 门面）；
- 连续天数：从 `daily_stats` 倒推连续日期，断一天归零；
- 待复习：M1 先显示 `words` 中 `status != 'mastered'` 的数量，M2 改为 `due_date <= today`；
- 累计生词：`WordRepo.count()`；
- 今日文章：存在则显示标题 + “继续阅读”，不存在则显示生成按钮。

### M1-7 集成与测试

新增测试建议：

- `tests/test_english_learner_storage.py`：建表幂等、CRUD、唯一约束、级联删除；
- `tests/test_english_learner_json_utils.py`：围栏 JSON、前后垃圾字符、缺失字段；
- `tests/test_english_learner_assessment.py`：mock AI 评分、解析失败回退、手动等级；
- `tests/test_english_learner_article.py`：合法/非法生成、重试、当天缓存；
- `tests/test_english_learner_reader.py`：offscreen 构造阅读器、双击取词（可只测纯函数）；
- GUI 冒烟：`MainWindow` 带临时 DB 能构造。

手工验收（用真实 Key）：

1. 删除配置 → 首次启动 → 配置 → 连接失败一次（故意填错 Key）→ 修改 → 成功；
2. 完成测评 → 首页显示等级与词汇量；
3. 今日页生成文章 → 进阅读器 → 双击 3 个单词 → 加入生词本；
4. 关闭应用重开 → 文章仍在、高亮仍在、生词本有 3 个词、没有重复调用生成；
5. 断网后打开应用 → 能看缓存文章与生词本，不崩溃。

### M1-8 阅读反馈调难度（M1.5 追加需求，已完成）

用户反馈“第一篇文章生词太多，打击积极性”，因此增加：

- 阅读页「换一篇」右侧新增「调整难度」；
- 弹窗展示当前等级、今日文章、今日生词；提供 4 个快捷选项 + 自由文本；
- 提交后在 TaskWorker 里执行 `LevelFeedbackService.adjust()`：
  1. AI 结合反馈与今日生词重新定级（太难至少降一档）；
  2. 按新等级 + 用户反馈重写文章，今日生词软约束复用；
  3. 原子替换当天文章（旧高亮清除、生词保留），保存新 profile；
- 失败时旧文章与旧画像保持不变，弹窗内显示原因，可重试或取消。

对应测试：`tests/test_english_learner_feedback.py`（12 个用例）+
存储层 `replace_by_date / list_highlighted_words / list_created_on`
+ 文章软约束与“换一篇失败保留旧文”+ GUI 冒烟。

## 8. M2 任务（已完成，记录实际交付）

> 状态（2026-11-19）：全部完成，验证见 5.3。实际实现与原始预告的差异如下。

- `services/srs.py`：简化 SM-2；四档 rating 1=忘记 / 2=模糊 / 3=认识 / 4=太简单；
  忘记时 `repetitions=0, interval=1, ease=max(1.3, ease-0.2)`；
  其余前两次固定 1 / 6 天，之后按易度增长；`interval >= 30` 标记 `mastered`；
  另加 `MAX_INTERVAL_DAYS=365` 上限，避免长期不复习后算出十年间隔；
- `storage/repositories.py`：新增 `ReviewRepo`（log / list_for_word / list_recent /
  count_for_date），`WordRepo` 新增 `save_schedule / update_meaning / delete`；
- `gui/pages/review.py`：概览 → 卡片 → 会话统计三态；空格/回车翻面、1~4 评分；
  一轮最多 200 词，复习记录与 `daily_stats.reviews_done` 同步落库；
- `gui/pages/vocabulary.py`：表格 + 筛选（全部/未复习/学习中/已掌握）+ 搜索 +
  右侧详情编辑中英文释义 + 删除（二次确认，级联清理高亮与复习历史）；
- `gui/pages/today.py`：「今日待复习」改用 `StatsRepo.count_due`；
  新增「开始复习」按钮，通过 `review_requested` 信号跳到复习页；
- 删除 `gui/pages/placeholders.py` 与 `common.PlaceholderPage`（占位页已全部替换）。

## 9. M3 任务（已完成核心项，记录实际交付）

> 状态（2026-11-19）：核心项 1~6 全部完成，验证见 5.4；可选项未做。

1. **数据导出** ✅：`services/export_service.py` + 设置页“数据导出”卡片，
   JSON 快照 / CSV 分表（M3.5 起为七表，M3.8 起为八表，含 `reading_quizzes`）；JSON 原子写入，
   CSV 用 `utf-8-sig`；
   仓库新增 `ArticleRepo.list_all`、`WordRepo.list_all_highlights`、
   `ReviewRepo.list_all`、`StatsRepo.list_all`；
2. **API Key 加密** ✅：`services/secret_store.py` 用 ctypes 调 Windows DPAPI，
   密文前缀 `dpapi:`；旧明文兼容，保存时自动升级；非 Windows / 加密失败回退明文；
3. **请求可取消** ✅：`TaskWorker` 新增 `cancelled` 信号、`cancel()`、`stop()`，
   页面统一 `shutdown()`，主窗口关闭时兜底 `wait()`；
4. **错误兜底与离线提示** ✅：`AIError.kind / retryable / is_offline`，
   网络/超时/429/5xx 可重试；今日页与复习页文案明确“复习离线可用”；
5. **生词本增强** ✅：排序（最新/最早/字母/到期日）、批量删除、
   未保存释义的脏检查（切页 / 筛选 / 搜索 / 排序 / 关闭窗口）；
6. **性能** ✅：生词本重建表格时暂停重绘，300 条词回归测试；
   复习队列上限 200 有测试；导出放后台线程；设置页加滚动区；
7. **未做（可选 M4）**：深色模式、安装器 / 自动更新。
   打包本身已在 M1 完成，`DailyEnglish.spec` 可直接复用。

## 13. M3.9 后续优化阶段（Phase 2 / Phase 3，已完成 → M3.11）

> 第一阶段（M3.9）已经把“AI 直接给等级”改成“AI 出证据 + 本地连续策略”。
> 下面两阶段用于把“单次检测”升级成“长期、可置信的水平估计”。
> **状态（2026-11-19）：Phase 2 / Phase 3 均已完成，实现细节见 1. 当前状态 M3.11
> 与 6.22；外部验收见 `VERIFICATION_M3.11.md`。** 下面保留原始任务书作为对照。

### Phase 2：本地证据模型 + 置信度（✅ 已完成）

目标：让等级变化基于多次观测的后验，而不是两次二值信号。

1. **数据结构**
   - 新增 `level_evidence` 表（或扩展 `reading_quizzes`）：
     `source`（assessment/feedback/quiz/review）、`observed_at`、
     `quiz_id`、`choice_correct/total`、`short_score`、
     `weighted_points`、`evidence_ratio`、`error_type_summary_json`、
     `posterior_json`、`confidence`。
   - `profile` 增加 `level_confidence REAL`、`level_updated_reason TEXT`；
     `pending_level/pending_count` 保留，作为后验未达阈值时的过渡状态。
2. **本地估计**
   - 用简单的 Rasch/逻辑斯蒂模型：
     `P(correct | L, d) = 1 / (1 + exp(k * (index(d) - index(L))))`，
     对 A2/B1/B2/C1 四个候选等级计算似然，再乘当前先验得到后验。
   - 题目难度由 AI 标注（已有 `difficulty`），本地只做算术；
     短答按一道同档/高档题计入。
   - 等级只在后验 MAP 与当前不同且 `confidence >= 0.6` 时改变；
     升级仍可要求连续两次，降级可一次。
3. **历史与长期信号**
   - 讲评 prompt 附带最近 5~10 次检测摘要（文章等级、得分、难度分布、错因），
     让 AI 做趋势判断；本地仍保留最终决定权。
   - 查词/生词本增加 `cefr_band` 或频率档（AI 查词时顺带标注）；
     统计用户查得最多、复习最稳的词档，作为弱证据。
   - 复习评分（`review_logs.rating`）按词档聚合，纳入证据模型。
4. **UI**
   - 结果页显示「B1（置信度 0.62）/ 建议 B2（观察中）」；
   - 今日页可显示等级趋势（最近 4 周），不做复杂图表，先文字/小条形。
5. **验收标准**
   - 纯函数估计器单测：全对/全错/混合难度/缺失难度/边界等级；
   - 两个用户场景回放：连续简单题不应快速升级、连续高难度题应升级；
   - 旧库 v5 → v6 原地迁移，旧检测结果不回填、不阻塞。

### Phase 3：自适应出题与更强诊断（✅ 已完成）

1. **两阶段自适应**
   - 先出 3 道当前等级题；根据正确率再出 2 道高一档或低一档题，
     用“边界探测”替代固定配比；代价是多一次出题调用。
2. **题目质量校准**
   - AI 出题后增加一次“题目审核”pass：检查答案唯一、干扰项合理、
     是否必须读文章才能答；不合格题丢弃/重出。
   - 记录每题历史正确率（本地统计），逐步形成文章题库的难度校准。
3. **更强产出信号**
   - 短答从 1 题扩展到 1 题短答 + 1 题句子改写/翻译（按等级可选）；
   - AI 评分 rubric 固定为“内容 / 语法 / 词汇”三项，减少主观漂移。
4. **盲评与反作弊**
   - 等级评估与讲评分两次调用：讲评模型看到当前等级；
     等级评估模型不看当前等级（blind），减少锚定偏差。
   - 选项随机排序、同一天只允许一次正式检测；重做只练手不调级。
5. **长期复盘**
   - 每周一次 AI 复盘：汇总检测、生词、复习数据，输出“本周水平判断 +
     下周建议”；用户确认后才改等级。
6. **验收标准**
   - 自适应题目的难度选择有单测（mock AI）；
   - 盲评分数与当前等级的相关性可用历史数据回放；
   - 任何 AI 失败都不改画像，用户可跳过/重试。
