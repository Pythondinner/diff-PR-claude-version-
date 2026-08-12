# 技术文档

记录 Ledger 层的实际实现：文件结构、配置、数据格式。架构决策的"为什么"见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 目录结构

```
diff_PR/                        # 代码"总部"——被监控的项目不需要有这份代码，只需要 Hook 指过来
├── .claude/
│   └── settings.json          # 项目级 Hook 配置（diff_PR 自己这个项目的）
├── ledger/
│   ├── hooks/
│   │   ├── _common.py         # 共享逻辑：读 stdin JSON、写日志（转调 _store，带上 cwd）
│   │   ├── log_user_prompt.py # UserPromptSubmit 挂的脚本
│   │   ├── log_tool_use.py    # PostToolUse 挂的脚本
│   │   └── remind.py          # Stop 挂的脚本：有积压就提醒一句，不调用大模型
│   ├── _text_safety.py        # 共享：UTF-8 IO 设置 + 隐形字符清洗
│   ├── _store.py               # 共享：按项目路径分区的读/写（load_records / append_record / events_file_for）
│   ├── _deepseek.py            # 共享：.env 加载 + DeepSeek HTTP 调用（所有项目共用一个 API Key）
│   ├── view_events.py         # 只读查看工具，格式化 + 截断超长字段
│   ├── confirm_intent.py      # 手动确认命令：提取需求意图，交互式确认
│   └── data/
│       └── projects/
│           └── <sanitized-project-path>/
│               ├── events.jsonl   # 每个被监控项目独立一份，运行后自动生成
│               └── memory.json    # Brain 的模式记忆，可变文件，不是追加写的账本
├── brain/
│   ├── review.py               # 手动审查命令：需求 vs 代码改动匹配度
│   ├── clear_backlog.py        # 批量清理待审查积压，不调用大模型
│   ├── _memory.py               # 共享：模式匹配、门槛判断、沉寂计算
│   └── memory.py                # 列出/删除记忆条目，不调用大模型
├── executor/
│   ├── report.py                # 手动生成命令：把 review_result 汇总成可读报告
│   └── report.md                # 生成的报告（运行后自动生成/覆盖，针对当前所在项目）
├── check.py                     # 唯一入口：自动接入项目（首次）+ 实时监控循环 + 空格键触发核查
├── .env                        # DEEPSEEK_API_KEY，已加入 .gitignore，不进版本库
└── docs/
    ├── ARCHITECTURE.md
    └── TECHNICAL.md
```

`_store.py` / `_deepseek.py` 是加 Brain 层时从 `confirm_intent.py` 里抽出来的共享模块（第三个调用方出现才抽象，不提前设计）。

## 按项目路径分区存储（`ledger/_store.py`）

`_store.py` 不再有一个固定的 `EVENTS_FILE` 路径，改成 `events_file_for(project_path=None)`：

- `sanitize_project_path(path)`：把文件夹路径转成安全目录名，规则是把非字母数字字符全部换成短横线——这个规则不是自己发明的，是照抄 Claude Code 自己 `~/.claude/projects/` 底下的命名习惯，验证过完全一致（`C:\Users\52396\Desktop\diff_PR` → `C--Users-52396-Desktop-diff-PR`，跟真实 Hook payload 里 `transcript_path` 用的目录名一模一样）
- `project_path` 不传的话，默认用 `Path.cwd()`——即脚本被调用那一刻实际所在的文件夹。这意味着 `confirm_intent.py`/`brain/review.py`/`check.py` 等手动命令，**必须在目标项目的文件夹里运行**才会操作到那个项目自己的数据
- Hook 脚本（`log_user_prompt.py`/`log_tool_use.py`/`remind.py`）不依赖 `Path.cwd()`，而是从 Hook 传入的 JSON payload 里读 `cwd` 字段——这是 Claude Code 自己给的、权威的项目路径，比假设子进程的系统 cwd 一定等于项目目录更可靠

`load_records()` / `append_record()` 都新增了可选的 `project_path` 参数，不传时行为不变（用当前 `cwd`），对 `confirm_intent.py`/`brain/review.py`/`executor/report.py`/`check.py`/`clear_backlog.py` 这些调用方完全透明，一行代码都不用改。

**数据迁移**：改造前 `ledger/data/events.jsonl` 里已经有 194 条 `diff_PR` 自己的真实历史记录，迁移到了新路径 `ledger/data/projects/C--Users-52396-Desktop-diff-PR/events.jsonl`，逐条核对过数量一致，没有丢失。

## 接入项目：融进 `check.py` 里，不再是独立命令

最初接入新项目是一条独立命令 `add_project.py`，用户反馈"记不住要先跑哪个"——直接启动 `check.py` 却忘了先接入，导致对着一个从没接过 Hook 的项目干瞪眼，看不到任何数据。既然"专注单项目"是当前的使用场景，没必要让用户区分"接入"和"核查"两个概念，合并成一个：

`check.py` 拿到项目路径之后，先用 `is_connected(project_path)` 检查这个项目的 `.claude/settings.json` 里是不是已经有了这三个 Hook（逐个事件比对 `command` 字符串是否都在），没有就自动调用 `connect_project(project_path)` 接上，再往下进入监控循环——用户只需要记住一条命令、一个路径，不用关心这个项目是第一次用还是已经用过很多次。

接入逻辑本身（`hook_config()`/`merge_hooks()`/幂等判断）跟原来 `add_project.py` 完全一样，只是从独立文件搬进了 `check.py`：**Hook 命令指向的还是这份共享代码**（`diff_PR/ledger/hooks/*.py` 的绝对路径），不复制任何文件；重复对同一个项目跑 `check.py`，`is_connected()` 会判断已经接过，不会重复写入。

实测过完整流程：拿一个全新的空文件夹跑 `check.py`，自动完成接入、打印提醒、无缝进入监控循环；再跑一次同一个文件夹，正确跳过接入步骤直接进监控——幂等性和自动接入都验证过。

**两条真实边界，接入时会打印出来提醒用户**：
1. **不会补录接入之前的历史对话**——事件驱动监控天然没法回头扫描过去，这是设计边界不是 bug。Claude Code 自己其实存了完整会话记录（Hook payload 里的 `transcript_path` 字段），理论上可以做一个"读取历史 transcript 回填"的功能，但目前没做，不算已经支持。
2. **如果目标项目里已经有一个正在运行的 Claude Code 会话，新接的 Hook 不一定会被那个会话自动发现**——Claude Code 的配置加载通常只在会话开始时读一次，运行中途新增的 Hook 配置可能要敲 `/hooks` 重新加载、或者重开会话才会生效。这是 Claude Code 平台自身的行为，不是这个项目能控制的，但接入时会主动提示这一点，不让用户自己去猜。

## Hook 配置（`.claude/settings.json`）

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python \"...\\ledger\\hooks\\log_user_prompt.py\"", "timeout": 15 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          { "type": "command", "command": "python \"...\\ledger\\hooks\\log_tool_use.py\"", "timeout": 15 }
        ]
      }
    ]
  }
}
```

两个 hook 都不返回 `decision: block`，退出码始终为 0——只记录，不干预 Claude Code 的正常流程。

## `events.jsonl` 记录格式

单个统一的追加写日志文件，每行一条 JSON 记录，用 `record_type` 区分类型：

```json
{"id": "...", "logged_at": "ISO8601 UTC 时间戳", "record_type": "user_prompt_submit" | "post_tool_use", "payload": { ... 原始 Hook 数据，不做任何字段挑选 ... }}
```

### `payload`（record_type = "user_prompt_submit"）实测字段

```
session_id, prompt_id, transcript_path, cwd, permission_mode, hook_event_name,
prompt,          # 用户原始输入文本（注意：不是 user_input，早期猜测的字段名是错的）
session_title
```

### `payload`（record_type = "post_tool_use"，tool_name = "Edit"）实测字段

在真实环境中触发一次 Edit 后抓到的实际数据（比官方文档描述的更完整）：

```
session_id, prompt_id, transcript_path, cwd, permission_mode, effort,
hook_event_name, tool_name, tool_use_id, duration_ms,
tool_input: { file_path, old_string, new_string, replace_all },
tool_response: {
  filePath, oldString, newString, originalFile,
  structuredPatch,   # 现成的 diff hunk 数组，Brain 层以后可以直接用，不用自己计算 diff
  userModified, replaceAll
}
```

### `payload`（record_type = "post_tool_use"，tool_name = "Write"）实测字段

比 `Edit` 简单，因为写的是整份文件而不是片段：

```
tool_input: { file_path, content }
tool_response: {
  type,             # "create"（新建文件）
  filePath, content,
  structuredPatch,  # 新建文件时是空数组 []，没有"改动前"可对比
  originalFile,     # 新建文件时是 null
  userModified
}
```

## 手动确认命令（`ledger/confirm_intent.py`）

用户手动运行，不会被任何 Hook 自动触发：

```bash
python ledger/confirm_intent.py
```

流程：
1. 读取 `events.jsonl`，找到最后一条 `intent_snapshot` 之后的所有 `user_prompt_submit` 记录，作为待确认窗口（第一次运行则是从头开始的全部记录）。
2. 把这些消息的原文拼接起来，调用 DeepSeek（`deepseek-chat` 模型），提示词要求提炼所有需求意图并以 JSON 返回（数量不设上限，见下）。
3. 在终端里把候选意图列出来，交互式确认：回车全部接受、`r` 重新分析一次。确认前会提示一句"有遗漏自己留意，本工具不支持逐条编辑"。
4. 确认结果追加写入 `events.jsonl`，`record_type = "intent_snapshot"`。

**`m`（手动总结）选项已去掉**：最初设计里可以手动输入自己总结的需求，实际用起来发现这个分支在管道测试崩溃时收到过一次空字符串输入，产生过一条 `confirmed_intents: []` 的空需求快照垃圾数据（用 `brain/clear_backlog.py` 清理掉了）——这类"让用户手动输入替代 AI 结果"的分支，风险和收益不成正比，去掉后交互更简单，出错的分支也更少。

### `payload`（record_type = "intent_snapshot"）字段

```
covered_message_ids,   # 这次窗口覆盖的 user_prompt_submit 记录 id 列表
candidate_intents,     # DeepSeek 给出的原始候选意图
confirmed_intents,     # 用户确认/手动总结后的最终版本
confirmation_method    # "accepted" | "manual"
```

### API Key 配置

`DEEPSEEK_API_KEY` 存在项目根目录的 `.env` 文件里（`KEY=VALUE` 一行一个），脚本启动时自己解析这个文件写入环境变量，不依赖额外的第三方库（没有用 `python-dotenv`，手写了几行解析逻辑）。`.env` 已经加入 `.gitignore`，不会被提交到版本库。

调用 DeepSeek 用的是标准库 `urllib.request` 直接发 HTTP 请求（OpenAI 兼容的 `/chat/completions` 接口），没有引入 `openai` 或 `requests` 这类第三方依赖——目前整个 Ledger 层不需要 `pip install` 任何东西。

## 文本安全：`ledger/_text_safety.py`

统一处理"编码正确性"和"看不见的乱码字符"两类问题，被 `ledger/` 下所有脚本共享：

- `setup_utf8_io()`：脚本入口调用一次，把 stdin/stdout 显式钉死成 UTF-8。**这是根治**——防止 Windows 按系统区域设置错误解码中文，从源头上不产生垃圾字符。
- `sanitize_text(s)` / `sanitize(value)`：清洗孤立代理字符（编码错误的残留）、BOM、零宽字符，并做 Unicode NFC 归一化。**这是兜底**——处理"意料之外"的输入（用户粘贴带来的隐形字符、DeepSeek 返回内容里的杂质），不用来掩盖读取方式本身的错误。
- `clean_io` 装饰器 / `safe_input()`：分别包装"读取函数的返回值"和 `input()`，把清洗动作接到具体的输入入口上。

**为什么不能只做清洗、不管读取方式**：清洗是无法区分"编码错误产生的垃圾字符"和"用户输入里恰好有的生僻字/符号"的，如果只依赖清洗兜底，读取方式本身的错误可能导致真实内容被静默过滤掉——不报错，比崩溃更难发现。所以两层缺一不可：读的时候先用对编码，清洗只处理编码正确之后仍然存在的隐形字符问题。

应用范围：`hooks/_common.py` 的 `read_stdin_json()` 用 `@clean_io` 装饰；`confirm_intent.py` 的 `main()` 入口调用 `setup_utf8_io()`，所有 `input()` 换成 `safe_input()`，DeepSeek 返回内容用 `sanitize_text()` 清洗；`view_events.py` 入口调用 `setup_utf8_io()`。

## Brain 层（`brain/review.py`）

用户手动运行，不会被任何 Hook 自动触发：

```bash
python brain/review.py
```

流程：
1. 读取 `events.jsonl`，找出所有 `intent_snapshot`，排除已经有对应 `review_result`（按 `intent_snapshot_id` 匹配）的，按时间顺序取第一条未审查的。
2. 圈出这条 `intent_snapshot` 对应的代码改动窗口：上一条 `review_result`（没有则从头）到这条 `intent_snapshot` 之间的所有 `post_tool_use` 记录。
3. 对每条改动只提取 `file_path` + `structuredPatch`（Edit）或"新建文件 + 字符数"（Write，不展开全文），组装成 prompt。
4. 调用 DeepSeek，要求对每条需求逐条判断 `matched`/`partial`/`missing` 并给理由，加一个总体结论。
5. **确认关卡**（跟 `confirm_intent.py` 同一套交互，但选项更少）：把 DeepSeek 的判断展示出来，用户三选一——回车直接确认、`r` 让 DeepSeek 重新审一次、**`s` 清除这条、不采纳判断**（写入 `overall: "skipped"`，不花一次 DeepSeek 调用就能清掉这条待审查记录）。`s` 是应对"这条需求已经被后续对话推翻/过时了，不值得认真审查"这种情况加的——攒的积压越多，这种情况越容易出现，而且**积压越久的记录，不只是需求过时，连它评判依据的代码快照也是旧的**（每条 `intent_snapshot` 只绑定它创建那一刻之前的代码改动，不会自动包含后来的新改动），所以没必要在这种记录上纠结判断准不准，直接 `s` 清掉最省事。

**去掉了"手动改写"（`m`）选项**：最初设计里 `m` 让用户自己重写总体结论和总结，实际用起来发现这活儿比 `confirm_intent.py` 里"用自己的话说需求"难得多——用户得读懂 DeepSeek 给的逐条判断、还要能准确概括一个总体结论，而且当时也没有一个好操作的界面支撑这件事，纯粹添麻烦。改成只在确认前提示一句"判断有遗漏或不准确自己留意，不支持逐条编辑，觉得离谱就 r 重审或 s 清除"，不强求用户当场纠正措辞。

## 批量清理积压（`brain/clear_backlog.py`）

```bash
python brain/clear_backlog.py
```

跟 `brain/review.py` 里的 `s` 不一样——`s` 是"先花一次 DeepSeek 调用看完判断，才决定要不要清除这一条"；这个命令是**完全不调用大模型**，直接把所有待审查的 `intent_snapshot` 列出来（展示的是用户当初确认过的需求原文，不是 AI 判断，所以看得明明白白清的是什么），一次性输入编号（比如 `1,3` 或 `all`）批量标记为 `confirmation_method: "bulk_skipped"`，免费、瞬间完成。

这个命令是为了解决两个真实问题加的：① 之前想清掉一堆过时积压，得一条条先花大模型调用看完判断才能清，浪费且啰嗦；② 一次真实测试中它还顺带揪出一条 `confirmed_intents: []` 的空脏数据（`m` 选项被移除前，一次管道测试崩溃留下的），证明"先列出来再批量选"这个方式比"一条条读判断"更适合处理这类清理工作。
6. 确认后把结果写回 `events.jsonl`，`record_type = "review_result"`。

一次只处理一条 `intent_snapshot`；如果有积压的多条，需要多跑几次 `brain/review.py`。

### 意图提取不再有数量上限

ADR-002 最初定的"1-5 条"上限已经放开（`confirm_intent.py` 的 `SYSTEM_PROMPT` 和 `parse_intents` 的兜底分支都改了），因为用户明确了"大核查"（攒很久再一次性确认/审查）会是常态用法，硬卡 5 条会导致细节需求被揉进笼统大类里丢失精度。

**放开之后的真实副作用**：窗口越宽，越可能把"你先说要 A、后来改口说不用 A 了"这种前后矛盾的表态都当成独立意图列出来（真实发生过一次：同一批待确认消息里同时出现"移除测试内容"和"保留测试内容，不介意"）。DeepSeek 不会自己判断哪句话被后面的话覆盖了。**这正是确认关卡存在的意义**——窗口越宽，确认时越不能无脑直接回车，需要真的扫一眼候选列表，把被自己后续发言推翻的条目筛掉，或者干脆用 `m` 手动总结。

### 为什么加确认关卡

最初版本 `review_result` 是 DeepSeek 判断完就直接写死进日志，用户没有把关的机会——这跟"判断权留给人，不搞全自动"的设计哲学不一致。虽然审查结果只是诊断信息、不会触发任何自动动作，但用户明确要求要有这道关卡，所以加上了，做法跟 `intent_snapshot` 的确认关卡保持同一套交互习惯（回车/`m`/`r`），不引入新的交互模式。

### 已修复的严重问题：新建文件时 Brain 完全看不到内容

`summarize_change()` 最初对 `Write`（新建文件）只报文件名和字符数，不给内容，是为了避免 prompt 膨胀。跑通整条链路测试时发现这个优化用错了地方：对全新文件，不给内容等于什么信息都没给，Brain 没法判断这个文件到底实现了什么，只能一律判 `missing`——真实复现过一次：新建 `check.py` 后审查，DeepSeek 给出"没有提供统一入口"的错误判断，原因就是它压根没看到 `check.py` 里写了什么。

修复：新建文件改成给完整内容，超过 4000 字符才截断（`WRITE_CONTENT_CAP`）。之前担心的"膨胀"，根源是 Ledger 里同一份内容存了两三遍，不是"给 Brain 一份内容"本身的问题——这里是把优化用过头了。修复后重新审查同一个 `check.py`，正确判定 `matched`，理由里明确提到了 `STEPS` 列表，证明这次是真的看到内容在做判断。

### 已知问题：`per_intent.intent` 有时会跟 `restated_intents` 重复

跑完整流程时发现的：prompt 一开始没约束 `per_intent[].intent` 该填什么，DeepSeek 有时候老实填原始需求原文，有时候把自己复述过的版本又填一遍，导致报告里"AI 理解到的需求"和"逐条判断"两处内容几乎重复。补了一句明确约束（必须填原文，不要填复述版本）后重新测试，重复问题解决了，但 `intent` 字段不是 100% 逐字跟原文一致（测试时模型丢了原文里的一个前缀）——这是 LLM 本身的局限，不追求靠 prompt 措辞彻底消除，只要不再跟 `restated_intents` 大段重复即可。

### 复述需求：给 DeepSeek 加了一道"理解检查"

Brain 的 prompt 现在要求 DeepSeek 先用自己的话把每条需求重新表述一遍（`restated_intents` 字段，格式 `"1: 用户想要..."`），再去逐条判断，不能照抄原文。这不是为了好看，是为了让"AI 理解错了需求"这种情况能被用户当场发现——如果 DeepSeek 复述的内容跟你的本意对不上，你在看到判断结论之前就能先看出来，而不是等它已经基于错误理解给出了一个看起来很像样的判断。`print_verdict()` 会把这段复述放在最前面展示。真实测过一次：DeepSeek 的复述是真正的改写，不是抄原文，说明这道检查是有效的。

### `payload`（record_type = "review_result"）字段

```
intent_snapshot_id,     # 审查的是哪条 intent_snapshot
reviewed_change_ids,    # 看过的 post_tool_use 记录 id 列表
ai_verdict: {            # DeepSeek 给出的原始判断，完整保留、不因为用户手动修改而丢失
  restated_intents: ["1: 用户想要...", ...],  # DeepSeek 用自己的话复述的需求理解
  per_intent: [{ intent, status, reason }, ...],
  overall, summary
},
confirmed_verdict: {     # 用户确认/修改后的最终版本，"accepted" 时跟 ai_verdict 完全一样
  per_intent, overall, summary
},
confirmation_method      # "accepted" | "manual"
```

**兼容提醒**：加确认关卡之前写入的 2 条 `review_result`（这次会话早些时候的测试记录）用的是旧字段名 `verdict`（没有 `ai_verdict`/`confirmed_verdict`/`confirmation_method` 的区分）。这是 append-only 日志的正常演进方式——不回头改写历史记录，Executor 层如果要读 `review_result`，需要兼容这两种字段形状。

## Executor 层（`executor/report.py`）

用户手动运行：

```bash
python executor/report.py
```

不调用大模型，纯本地读取当前项目的 `events.jsonl` 里所有 `review_result`，重新生成一份报告——**每次运行是重新生成、覆盖旧文件，不是追加历史**，`events.jsonl` 已经是永久历史记录，报告只是它的一个可读投影，不重复存一份数据源。

**报告文件按项目分区存放**：`executor/reports/<sanitized-project-path>/report.md`，跟 `ledger/data/projects/` 用同一套路径清洗规则。这是接入多项目支持（Task #4）之后，拿一个真实新项目（`diff_test_v0`）测试时发现的问题——最初报告文件写死在 `executor/report.md` 一个固定位置，查完一个项目的报告会被查另一个项目时覆盖掉，两个项目的报告会串在一起、文件名也看不出是哪个项目的，已经修复。

内容结构：
- 顶部总览：一共审查过几次、完全匹配/部分匹配/没做到/已跳过各多少
- "需要关注"（部分匹配 + 没做到，按时间倒序，永远展开）排在最前面
- "已完全匹配 / 已跳过"放在 `<details>` 折叠块里，不占视觉焦点——跳过的记录代表用户已经主动决定"不用管了"，跟完全匹配一样不需要再关注
- 每条审查展示：AI 复述的需求理解（`restated_intents`，兼容没有这个字段的老记录）、逐条判断、涉及改动的文件链接（`file:///` 协议，点开能在系统默认程序里打开）

**链接的诚实局限**：项目没有用 git，做不到"点开看当时那次改动的历史 diff"，链接只能打开文件*现在*的样子——如果文件之后又被改过，点开看到的就不是审查那一刻的内容了。报告里每条审查都带了一句提示，真要看精确的历史改动用 `ledger/view_events.py` 查（`structuredPatch` 存的是那一刻的精确 diff）。

兼容两种 `review_result` 格式：加确认关卡之前的老记录用 `payload.verdict`，之后的用 `payload.confirmed_verdict`，读取时按顺序尝试。

## 统一入口 `check.py`：状态展示和触发审查合并成一个命令

```bash
python check.py
```

最初 `status.py`（只看状态）和 `check.py`（触发审查）是两个独立文件，用户反馈想要"打开 check.py 就能看到交互状态，某个阶段直接输入指令核查"的体验——于是把 `status.py` 的展示逻辑合并进了 `check.py`，`status.py` 已删除。

现在的流程：
1. **开场先问要锁定监控哪个项目文件夹**（`resolve_project_path()`）——**必须显式输入完整路径，不接受直接回车用默认值**。这是为了防止手滑按了回车、在没意识到的情况下锁定了错误的项目（比如当前所在目录刚好不是你想查的那个）；空输入或者路径不存在都会重新问，直到给出一个真实存在的文件夹。这样 `check.py` 也不用非得在目标项目文件夹里运行才能查它，在哪运行都行，只要明确告诉它要看哪个项目
2. **检查这个项目有没有接过监控**（`is_connected()`），没有就自动接上（`connect_project()`，逻辑等同原来独立的 `add_project.py`，见上一节）——不用用户先手动跑一遍别的命令
3. 进入**实时监控循环**（`main()` 最外层 `while True`）：每 `POLL_SECONDS`（2 秒）重新数一遍，数字变了才刷新打印，不刷屏，不调用大模型。**就算当下什么都不用查也不会自动退出**——会一直停在监控状态，用户可以晾着它，正常跟 Claude Code 聊天、改代码，数字会自己跳
4. 监控循环期间，按**空格键**（`msvcrt.kbhit()`/`getch()`，Windows 标准库，非阻塞、不用等回车）表示"看看要不要核查"，跳出监控进入确认环节
5. 确认环节：如果当下确实有待确认/待审查的内容，问一句要不要现在核查（输入指令词 `y`/`核查` 才会真正调用 DeepSeek，其他输入都是"先不查"）；如果按空格时其实什么都没有，提示一句就回到监控。**不管是"先不查"、核查完，还是核查中途出错，最后都会回到监控循环，不会退出整个程序**
6. **唯一真正退出程序的方式是 Ctrl+C**——在 `main()` 最外层统一用 `try/except KeyboardInterrupt` 捕获，不管当时是在监控循环里还是在等确认输入，按下就退出

**锁定项目路径怎么在子进程里生效**：`run_checks()` 调用 `subprocess.run(..., cwd=str(project_path))`，显式把子进程（`confirm_intent.py`/`brain/review.py`/`executor/report.py`）的系统工作目录设成锁定的项目路径，而不是依赖 `check.py` 自己的运行位置——这样这几个子脚本里默认"用 `Path.cwd()` 取当前项目"的逻辑（见 `_store.py` 那节）拿到的就是你锁定的那个项目，不是你敲命令时随便所在的哪个文件夹。实测过：指向一个空的测试文件夹，正确显示 0 条记录，跟 `diff_PR` 自己的真实数据（几十条）完全区分开，锁定生效。

这版是根据用户实测反馈改的：上一版把"取消核查"和"退出整个程序"混成了一件事——不管确认环节选什么，程序都会退出，跟"应该一直开着，只有明确退出才关闭"的要求不符。现在退出程序只有 Ctrl+C 一条路，其他任何操作路径最终都会绕回监控循环。

**测试局限说明**：空格键检测和 Ctrl+C 中断，在这个项目使用的 Windows + Git Bash 沙盒测试环境里没法被完整模拟验证（`msvcrt` 依赖真实的 Win32 控制台，管道/信号在 Git Bash 下的行为跟真实终端不一致）——已验证的是：脚本能正常启动、循环结构和计数刷新逻辑正确、`msvcrt.kbhit()` 调用不报错；实际按键体验需要用户自己在真实终端（比如 PowerShell）里验证。

## 轻量提醒（`ledger/hooks/remind.py`，挂在 `Stop` 事件）

每轮 Claude Code 说完话触发一次。做三个计数：上次确认之后有几条新对话、上次审查之后代码改了几次、有几条 `intent_snapshot` 还没被审查。任意一个数大于 0，就通过 Hook 的 JSON 输出 `{"systemMessage": "..."}` 提示一句（比如"2 条新对话，10 次代码改动——想核查随时运行 python check.py"），三个都是 0 就完全不输出（沉默，不产生噪音）。不读取任何一句话的具体内容，不调用 DeepSeek，跟 `log_user_prompt.py`/`log_tool_use.py` 一样是"几毫秒跑完退出"的量级——这就是用户想要的"能看到数据流、但不涉及自动审查"的中间态：数字是自动、实时冒出来的，是否要花一次大模型调用去真正核查，决定权还在用户手上。

**这里故意反着用了编码套路**：不是调 `setup_utf8_io()`，而是让 `json.dumps` 用默认的 `ensure_ascii=True`，把中文转成 `\uXXXX` 转义——输出的字节全是纯 ASCII，从根上绕开这台机器上反复出现的"stdout 不是 UTF-8"这类坑，不需要再显式配置编码。

## Brain 记忆层（`brain/_memory.py` + `brain/memory.py`）

### 轮次编号

`ledger/confirm_intent.py` 新增 `next_round_number(records)`：这个项目里已经有几条 `intent_snapshot` 就是第几轮（从 1 开始），写进 `intent_snapshot.payload.round_number`。`brain/review.py`/`brain/clear_backlog.py` 生成 `review_result` 时，从对应的 `intent_snapshot` 里读出同一个 `round_number` 沿用下去，不重新计算。

### `memory.json`：唯一的可变状态文件

跟 `events.jsonl` 存在同一个项目分区目录（`ledger/data/projects/<项目路径>/memory.json`），复用 `_store.py` 的分区机制，但读写方式不同——`_store.py` 新增 `memory_file_for()`/`load_memory()`/`save_memory()`，是整份读、整份覆盖写，不是追加。这是这个项目里唯一一处允许"改历史"的地方，因为它存的是"当前认为成立的模式"，不是不可变的事实记录，跟 Ledger 的账本性质不同，是刻意的例外。

单条记忆的结构：
```
{
  "id": "8位短id",
  "pattern": "模式描述",
  "status": "candidate" | "confirmed" | "declined",
  "occurrences": N,
  "rounds": [N1, N2, ...],
  "first_seen_round": N, "last_seen_round": N,
  "created_at": "...", "updated_at": "..."
}
```

### 匹配和提议逻辑（`brain/_memory.py`）

- `format_known_patterns(memory_list, current_round)`：组装成喂给 DeepSeek 的"已知模式"文本，**已确认和候选的都给**——候选也要给，不然候选永远等不到第二次被匹配的机会。已确认的模式如果 `current_round - last_seen_round > DORMANT_AFTER_ROUNDS`（5）就不再喂给 DeepSeek（沉寂），但记录本身不删除，`brain/memory.py` 还能查到。
- `brain/review.py` 的 `SYSTEM_PROMPT` 扩展了第三步：要求 DeepSeek 判断这次的问题是否符合已知模式（给 `matched_pattern_ids`），或者是否值得提一个新候选（`new_candidate_pattern`，没有就填 `null`，明确要求"不确定就宁可填 null，不要为了凑数硬造模式"）。
- `apply_matches()`：原地更新 `memory_list`——命中的模式累加 `occurrences` 和 `rounds`；有新候选就新建一条（状态 `candidate`，`occurrences=1`）。返回这次新跨过"提议门槛"（`PROPOSE_THRESHOLD = 2`）的候选列表。
- **只有用户确认了审查结果（选了回车），才会进行记忆更新**——选 `s` 清除的审查不计入模式统计，因为"清除"本身就代表用户不认可这次的判断。
- 候选跨过门槛后，`brain/review.py` 会在审查结果确认完之后单独问一句"发现一个可能反复出现的模式：xxx——要记住吗？[y]/其他"，`y` 变成 `confirmed`，其他任何输入变成 `declined`（不会被删除，只是以后不会再被喂给 DeepSeek，`brain/memory.py` 还能看到）。

### 真实验证过语义匹配

构造了两轮真实调用（不是单元测试的假数据）：第一轮场景是"改 `config.yaml` 加超时参数，但没同步更新 `docs/CONFIG.md`"，DeepSeek 主动提出候选模式"修改配置时遗漏同步更新相关文档"；第二轮换成完全不同的措辞（"改 `settings.py` 加连接池参数，没同步更新 `docs/DATABASE.md`"），喂给它第一轮产生的候选模式作为上下文，DeepSeek 正确识别出这是同一类问题，返回了对应的 `id`——证明匹配是真实的语义理解，不是字符串比对出来的巧合。

### 观测入口

- `brain/review.py`/`executor/report.py` 展示审查结果时，如果引用了记忆，会加一行"引用了历史模式：xxx（出现在第 A、B 轮）"
- `brain/memory.py`：列出所有记忆条目（候选/已确认/已回绝分开标注状态），支持按编号删除，不调用大模型，交互习惯照抄 `clear_backlog.py`

## 已知问题与修复

**Windows 下 stdin 中文乱码 / `UnicodeEncodeError`**：Python 在 Windows 上 `sys.stdin.read()` 默认按系统区域编码（非 UTF-8）解码，遇到中文会产生非法代理字符导致写入崩溃。

修复：改用 `sys.stdin.buffer.read().decode("utf-8")` 读取原始字节并显式按 UTF-8 解码（见 `ledger/hooks/_common.py`），不依赖系统区域设置。Claude Code 传给 Hook 的数据本身是 UTF-8，这样读取才能跟实际发送的编码对上。

**同一个病根在 `confirm_intent.py` 的交互式 `input()` 里又出现过一次**：手动总结分支（`m`）第一次真实测试时，用户从终端输入的中文经 `input()` 读取后同样产生非法代理字符，写入 `intent_snapshot` 时崩溃。修复：在 `main()` 开头加一行 `sys.stdin.reconfigure(encoding="utf-8")`，跟 `sys.stdout.reconfigure(encoding="utf-8")` 配对，把这个脚本里所有标准输入输出都钉死成 UTF-8。**结论：在这台 Windows 环境下，任何新写的、涉及 stdin/stdout 读写中文的脚本，都要默认加上这两行，不能假设 Python 会自己选对编码。**

## 验证记录

### 已验证（真实跑通，不是纸面设计）

- `python -m json.tool .claude/settings.json`：语法校验通过
- `UserPromptSubmit` / `PostToolUse`（Edit、Write 两种工具）均已被真实触发多次并正确写入 `events.jsonl`（累计 21 条记录：13 条 post_tool_use、7 条 user_prompt_submit、1 条 intent_snapshot）
- matcher 范围生效：会话中大量 Read/Bash/Grep 等操作没有产生任何 post_tool_use 记录，说明 `Edit|Write|NotebookEdit` 的限定没有漏记也没有多记
- `view_events.py` 格式化查看：中文正常显示（stdout 编码问题已修复）
- `confirm_intent.py` 主流程（"全部确认"分支）：真实调用 DeepSeek，提取意图并写入 `intent_snapshot`
- `confirm_intent.py` 的窗口边界逻辑：验证过连续两轮运行，第二轮正确地只捞到上次确认之后的新消息（1 条），没有重复带上已确认过的旧消息
- `confirm_intent.py` 的 `r`（重新分析）和 `m`（手动总结）分支：均已真实跑过。`r` 分支正常；`m` 分支第一次跑暴露了 stdin 编码 bug（见上），修复后重跑验证通过，手动输入的中文内容正确落盘
- `brain/review.py` 三种状态全部真实跑过：① 有积压时正确取最早一条未审查的 `intent_snapshot`，调用 DeepSeek 给出有依据的逐条判断（包括正确识别出"数据接口对接"这条需求只完成了文档规划、没有实际打包分发，判定 `partial`，跟我们自己的设计决策吻合）；② 连续第二次运行正确跳到下一条未审查的快照，没有重复审查同一条；③ 两条都审查完之后，第三次运行正确报告"没有待审查的"，checkpoint 逻辑（`review_result` 关联 `intent_snapshot_id`）验证通过
- `brain/review.py` 的确认关卡：`r`（重新审查）分支真实跑过，重新调用后判断刷新；`m`（手动改写）分支真实跑过，`confirmed_verdict` 正确落盘为用户输入的值，`ai_verdict` 完整保留 DeepSeek 的原始判断没有被覆盖。测试过程中意外发现一个真实的健壮性案例：DeepSeek 第一次返回的 JSON 里 `overall`/`summary` 字段缺失，`print_verdict` 靠 `.get()` 默认值兜住了没有崩溃（显示"未知"），重新审查后第二次响应就正常了——说明 DeepSeek 不是每次都严格按 prompt 要求的字段返回，`.get()` 兜底是必要的，不是防御过度
- `[回车]`（直接确认）分支没有在这次会话里专门造数据测试，但它是三条路径里最简单的一条（`ai_verdict` 原样赋给 `confirmed_verdict`），复用的是跟 `m`/`r` 分支相同、已经验证过的 `append_record` 调用

### 尚未验证 / 已知缺口

- `NotebookEdit` 工具从未被真实触发过，实际 payload 字段未知（矩阵里配了 matcher，但没有真实样本）
- 并发写入安全性没有专门测试：多个 Hook 幾乎同时触发时是否会互相覆盖 `events.jsonl` 的写入，目前用简单的 `open(..., "a")` 追加、没加文件锁（当前场景下工具调用是顺序执行的，风险低，但没有压测验证）
- Hook 脚本自身抛异常时 Claude Code 的实际表现没有主动测试过（理论上退出码非 0/2 会被当成非阻塞错误提示给用户，但没有验证过具体呈现效果）
- **`events.jsonl` 体积膨胀**：`Write` 记录会把整份文件内容存两次（`tool_input.content` 和 `tool_response.content`），文件越大、写入次数越多，日志涨得越快，目前没有任何精简机制——这是一个明确但还没处理的技术债，不是"顺手就能忽略"的小问题

## 下一步（未实现）

- `NotebookEdit` 工具的真实 payload 结构（从未真实触发过）
- Executor 层：温和提示模式，把 `review_result` 用更易读的方式展示给用户（目前 `brain/review.py` 已经会在终端打印，Executor 要做的是在此基础上把体验做得更友好，比如汇总多条 `review_result`）
- `brain/review.py` 目前一次只处理一条 `intent_snapshot`，有积压需要多次运行；是否要支持一次性处理所有积压，留到实际用起来觉得不够用再加
