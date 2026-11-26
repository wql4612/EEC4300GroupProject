# D2 Boolean basic Prompt 对照实验报告

**2026-11-26｜5 个开发组、4 查询/组、3 种子、3 个 Prompt。**

**P2 是后续开发的首选候选，当前没有版本通过 Pilot 的 Prompt 放行门槛。** P2 的官方 RR 为 14.00%，高于 P1 的 2.67% 和 P0 的 0.00%。P2 的官方 VR 为 16.67%，低于 25% 门槛，且三个种子分别只有 2、2、3 个组产生有效候选。

## A. 请求数量与基础设施

计划 **180** 个槽，实际发送 **180** 次。HTTP 2xx **180/180（100%）**，Ollama 完成响应 **180/180（100%）**。所有响应都是 HTTP 200。重跑 **0** 个配对块、**0** 次请求。每个版本实际 60 次。

配对块为同一观测组与同一 rollout seed 的三种 Prompt 各四槽。组内按槽交错发送，三个版本对相同槽复用同一个模型 seed。冻结的重跑规则是单块至少两次基础设施故障时重跑整块一次，完整保留两次轨迹。本次没有触发。每次请求的模型摘要、温度、模型 seed、模板和渲染 Prompt 哈希、输出限制及原始 HTTP 正文均保存在请求日志。

## B. 完整 Prompt 与 SHA-256

哈希基于 UTF-8、LF 的完整模板。下方附录保存本次冻结的完整文本。每条请求的已渲染完整文本和相应 SHA-256 在原始日志内。

| 版本 | 模板 SHA-256 |
| --- | --- |
| [P0](../prompts/d2/P0.txt) | `2a4d50ce196564d295b6872355ccfbe9f376f0f2b7334e246ae0e0e6855b18dc` |
| [P1](../prompts/d2/P1.txt) | `e6b0498c7866f667218ff16f163240d766439e052c84507e5a2a3911d71f36ab` |
| [P2](../prompts/d2/P2.txt) | `5811d33e71fdafc0eaf59d10e6d50695480f8cef3959f3096072ba31cad6d265` |

P0 完全沿用固定上游。P1 只修复示例，保留原四条示例的顺序和位置。P2 继承 P1，明确限制 AND/OR，追加 `x AND y`、`x OR y` 的格式例并要求匹配本组全部观测。P2 两项变更作为组合评估。 [逐行差异](../prompts/d2/README.md)

## C–G、I. 汇总指标

| 版本 | Parse | Guard | In-space | Official VR | Official NR | Official RR | First valid novel | NOT/NOR | P2 示例键 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| P0 | 100.00% | 98.33% | 1.67% | 0.00% | 1.67% | 0.00% | 0.00% | 98.33% | 0.00% |
| P1 | 100.00% | 100.00% | 48.33% | 3.33% | 28.33% | 2.67% | 3.33% | 50.00% | 40.00% |
| P2 | 100.00% | 100.00% | 36.67% | 16.67% | 28.33% | 14.00% | 15.00% | 63.33% | 30.00% |

- **Parse** 与 **In-space** 分别取官方 `parse_success_rate` 与 `in_space_rate`。**Guard** 使用 D1 语法检查，仅作独立诊断，允许 NOT 的语法形态经过。它不会替换官方评分输入。
- **Official VR、NR、RR** 直接取固定版本 `evaluate_single_observation_set(..., max_retries=1)` 的 `valid_rate`、`novelty_rate`、`recovery_rate`。先按每个种子 × 观测组评分，再对 15 条轨迹取平均。NR 的分子是空间内不同机械键数，包含与观测不一致的空间内候选。RR 匹配的是官方答案键。
- **First Valid Novel Discovery Rate** 为各组及各 rollout 自身首次出现的有效机械键次数除以全部槽位，再对 15 个等长轨迹平均。重复有效键计入 VR，但不会计为再次发现。
- **NOT/NOR** 按原始模型文本是否含独立算符词统计。同一响应出现多个只计一次。**P2 示例键**按官方机械键命中 `x AND y` 或 `x OR y` 统计，分母为全部槽位，含未通过观测一致性的候选。三个版本用同一对键计量，因此频率也包含 P1 已有示例的影响。

| 版本 | NOT | NOR | 命中 P2 示例键 | 命中 P2 示例键且有效 |
| --- | ---: | ---: | ---: | ---: |
| P0 | 59/60 | 0/60 | 0/60 | 0/60 |
| P1 | 30/60 | 0/60 | 24/60 | 0/60 |
| P2 | 38/60 | 0/60 | 18/60 | 6/60 |

仅看没有 prior history 的首槽，P0、P1、P2 含 NOT 的响应分别是 **15/15、1/15、5/15**，符合观测的响应分别是 **0/15、1/15、5/15**。合并四槽后，P2 比 P1 更常输出 NOT，但有效率更高。现有设计支持比较模板干预的整体表现，无法独立归因给某条约束或某个示例。

## H–J. 各种子、各观测组及首个发现位置

开发组按可见观测条数分层选取。D1 已用组 `n3_inputs_01_10_11_outputs_001` 已排除。开发 5 组、Pilot 留出 5 组、正式留出 15 组，经 ID 与观测指纹核对互不重叠。后两个划分仅冻结清单。

| 组 | Observation Group ID |
| --- | --- |
| G1 | `n1_inputs_10_outputs_1` |
| G2 | `n2_inputs_10_11_outputs_11` |
| G3 | `n2_inputs_00_10_outputs_01` |
| G4 | `n3_inputs_01_10_11_outputs_111` |
| G5 | `n4_inputs_00_01_10_11_outputs_0101` |

### Seed 42

| 组 | 版本 | Parse | Guard | In-space | Official VR | Official NR | Official RR | First valid novel | NOT/NOR | P2 示例键 | 首次有效 | 首次有效新键 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| G1 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G1 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G1 | P2 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G2 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G2 | P1 | 100.00% | 100.00% | 100.00% | 25.00% | 50.00% | 20.00% | 25.00% | 0.00% | 75.00% | 3 | 3 |
| G2 | P2 | 100.00% | 100.00% | 100.00% | 75.00% | 75.00% | 40.00% | 50.00% | 0.00% | 50.00% | 1 | 1 |
| G3 | P0 | 100.00% | 75.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G3 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G3 | P2 | 100.00% | 100.00% | 75.00% | 50.00% | 75.00% | 40.00% | 50.00% | 25.00% | 50.00% | 1 | 1 |
| G4 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G4 | P1 | 100.00% | 100.00% | 75.00% | 0.00% | 50.00% | 0.00% | 0.00% | 25.00% | 50.00% | >4 | >4 |
| G4 | P2 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G5 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G5 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G5 | P2 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |

### Seed 271828

| 组 | 版本 | Parse | Guard | In-space | Official VR | Official NR | Official RR | First valid novel | NOT/NOR | P2 示例键 | 首次有效 | 首次有效新键 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| G1 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G1 | P1 | 100.00% | 100.00% | 100.00% | 0.00% | 25.00% | 0.00% | 0.00% | 0.00% | 100.00% | >4 | >4 |
| G1 | P2 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G2 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G2 | P1 | 100.00% | 100.00% | 50.00% | 0.00% | 25.00% | 0.00% | 0.00% | 50.00% | 50.00% | >4 | >4 |
| G2 | P2 | 100.00% | 100.00% | 25.00% | 25.00% | 25.00% | 20.00% | 25.00% | 75.00% | 25.00% | 1 | 1 |
| G3 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G3 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G3 | P2 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G4 | P0 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 0.00% | >4 | >4 |
| G4 | P1 | 100.00% | 100.00% | 75.00% | 0.00% | 25.00% | 0.00% | 0.00% | 25.00% | 75.00% | >4 | >4 |
| G4 | P2 | 100.00% | 100.00% | 100.00% | 25.00% | 50.00% | 50.00% | 25.00% | 0.00% | 100.00% | 4 | 4 |
| G5 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G5 | P1 | 100.00% | 100.00% | 75.00% | 0.00% | 50.00% | 0.00% | 0.00% | 0.00% | 25.00% | >4 | >4 |
| G5 | P2 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |

### Seed 314159

| 组 | 版本 | Parse | Guard | In-space | Official VR | Official NR | Official RR | First valid novel | NOT/NOR | P2 示例键 | 首次有效 | 首次有效新键 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| G1 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G1 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G1 | P2 | 100.00% | 100.00% | 25.00% | 25.00% | 25.00% | 20.00% | 25.00% | 75.00% | 0.00% | 1 | 1 |
| G2 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G2 | P1 | 100.00% | 100.00% | 25.00% | 25.00% | 25.00% | 20.00% | 25.00% | 75.00% | 0.00% | 1 | 1 |
| G2 | P2 | 100.00% | 100.00% | 75.00% | 25.00% | 50.00% | 20.00% | 25.00% | 25.00% | 75.00% | 2 | 2 |
| G3 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G3 | P1 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G3 | P2 | 100.00% | 100.00% | 50.00% | 25.00% | 50.00% | 20.00% | 25.00% | 50.00% | 50.00% | 1 | 1 |
| G4 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G4 | P1 | 100.00% | 100.00% | 75.00% | 0.00% | 25.00% | 0.00% | 0.00% | 25.00% | 75.00% | >4 | >4 |
| G4 | P2 | 100.00% | 100.00% | 25.00% | 0.00% | 25.00% | 0.00% | 0.00% | 75.00% | 25.00% | >4 | >4 |
| G5 | P0 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G5 | P1 | 100.00% | 100.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 100.00% | 0.00% | >4 | >4 |
| G5 | P2 | 100.00% | 100.00% | 50.00% | 0.00% | 25.00% | 0.00% | 0.00% | 50.00% | 50.00% | >4 | >4 |

`>4` 表示四槽均未出现。两项首次位置本次完全一致，因为每条轨迹从空档案开始，且首次有效表达式均有可用机械键。

| 版本 | 槽 1 | 槽 2 | 槽 3 | 槽 4 | >4 |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 0 | 0 | 0 | 0 | 15 |
| P1 | 1 | 0 | 1 | 0 | 13 |
| P2 | 5 | 1 | 0 | 1 | 8 |

## K. 模型耗时、显存与失败案例

从首请求开始到末请求完成为 **63.42 秒**。Ollama **0.40.14**，模型 `qwen2.5:3b` Q4_K_M，摘要 `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`。请求固定 `temperature=0.6`、`num_ctx=4096`、`num_predict=128`。

RTX 4060 Laptop GPU 总显存 **8188 MiB**。1 秒间隔采得 **54** 个完整样本，整卡显存使用 **2676–4861 MiB**，峰值 GPU 利用率 **93%**。停止采集时最后一条仅写入部分字段，已从汇总排除。Ollama 完成后报告模型为 100% GPU、上下文 4096。

| 版本 | HTTP 中位（秒） | HTTP P95（秒） | Ollama 平均总耗时（秒） | 输入 tokens | 输出 tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 0.271 | 0.366 | 0.362 | 32,684 | 966 |
| P1 | 0.262 | 0.440 | 0.258 | 32,552 | 1,026 |
| P2 | 0.278 | 0.414 | 0.370 | 37,073 | 1,021 |

HTTP 计时为请求发送到读完响应的墙钟时间，P95 使用最近秩。Ollama 总耗时来自每个响应的 `total_duration`，含模型加载开销。首个 P0 请求承担了冷载。显存为整卡采样值。

| 版本、组、种子、槽 | 封存原文 | 判定 |
| --- | --- | --- |
| P0 s1-g1 q1 | `Expression: (x AND y) OR (NOT x AND y)` | 包含 NOT，空间外。格式可提取且通过 Guard。 |
| P1 s1-g1 q1 | `Expression: x AND y OR (x AND y)` | 空间内，机械等价于 x AND y。在 (x=1, y=0) 时输出 0，与观测的 1 不一致。 |
| P2 s1-g2 q3 | `Expression: (x AND y) OR ((x OR x) AND (y OR y))` | 空间内，机械等价于 x AND y。在 (x=1, y=0) 时输出 0，与观测的 1 不一致。 |
| P0 s1-g3 q2 | `Expression: X OR NOT Y OR X AND Y` | 大写变量触发 Guard 拒绝和离线词法复核。经人工审阅后，官方 Evaluator 收到未修改的原文，提取成功、空间外。 |

## L. 推荐与 Pilot 判定

| 版本 | VR ≥ 25% | 每种子至少一个有效候选的组数，按 42 / 271828 / 314159 | 是否放行 |
| --- | --- | --- | --- |
| P0 | 0.00% | 0/5、0/5、0/5 | 否 |
| P1 | 3.33% | 1/5、0/5、1/5 | 否 |
| P2 | 16.67% | 2/5、2/5、3/5 | 否 |

预设放行要求汇总 VR ≥ 25%，且至少两个种子各有至少 4/5 组产生有效候选。P2 在 RR、VR 和首次有效新键率上领先，可作为复审时继续开发的候选。三个版本都未达放行条件。现阶段保留冻结的 Pilot 与正式划分。

## 核验与复现

在线工作进程只加载去标签 manifest。开始请求前封锁新文件读取和子进程，实际对原始答案文件的打开失败。隔离在 Python 工作进程层执行。离线进程从 HTTP 正文字节核对封存响应，将原模型文本原样送回官方 Evaluator；Guard verdict 独立记录。大写变量夹具的只读人工复核见回放审阅记录。采集日志和评分文件分别保留。

- [启动前十项检查和 16 项离线测试结果](../results/d2/prompt-20261126/preflight.json)
- [冻结配置、模型摘要和哈希](../results/d2/prompt-20261126/freeze.json) · [分组清单](../results/d2/prompt-20261126/split_manifest.json) · [采集 manifest](../results/d2/prompt-20261126/online_manifest.json)
- [封存 HTTP 请求和响应](../results/d2/prompt-20261126/requests.jsonl) · [采集日志](../results/d2/prompt-20261126/collection.log)
- [官方结果和逐槽诊断](../results/d2/prompt-20261126/official-results.json) · [逐种子和逐组 CSV](../results/d2/prompt-20261126/group-seed-metrics.csv)
- [GPU 采样](../results/d2/prompt-20261126/gpu-samples.csv) · [Ollama 结束状态](../results/d2/prompt-20261126/ollama-after.txt) · [原文安全复核记录](../results/d2/prompt-20261126/reviewed-replay-expressions.json)

复算现有日志（无模型请求）

```powershell
$env:PYTHONHASHSEED = '0'
.\.venv\Scripts\python.exe -B -m scripts.score_d2_prompt results/d2/prompt-20261126
```

## 附录：本次使用的完整模板

`{{OBSERVATIONS}}` 和 `{{PRIOR_EXPRESSIONS}}` 在实际请求前替换成可见观测及本 Prompt 的组内候选历史。完整渲染原文以请求日志为准。

### P0

```text
You are given partial observations of a Boolean function with variables: x, y

            Allowed operators: AND (conjunction), OR (disjunction)

            Observations (input -> output):
            {{OBSERVATIONS}}

            Prior expressions generated (avoid repeating any expression 
            that is equivalent under the rules below):
            {{PRIOR_EXPRESSIONS}}

            Task: Generate a single Boolean expression that is consistent with ALL observations.

            Requirements:
            1. Use ONLY the variables: x, y
            2. Use ONLY these operators: OR, AND
            3. The expression must match all given observations
            4. Structural uniqueness is judged by these rules:
            - Commutativity: reorderings are the SAME (e.g., x AND y = y AND x).
        - Idempotence: duplicates of the same input under AND/OR collapse (e.g., x AND x = x; x OR x = x).
        - Associativity flattening: cascades of the SAME operator are the SAME regardless of parentheses (e.g., (x AND y) AND x = x AND (y AND x) = x AND x AND y).
        - No distributivity, absorption, or De Morgan normalization: mixing operators keeps expressions DIFFERENT (e.g., (x AND y) OR z ≠ x AND (y OR z)).
            5. Expression depth should be at most 2 levels of nesting
            6. Do not use boolean constants True or False anywhere in the expression.

            Output format: 
            - Return ONLY the Boolean expression on a single line
            - Use plain text format (no LaTeX, no markdown, no special formatting)
            - Use uppercase for operators. 
            - Use lowercase for variables: x, y
            - Use parentheses for grouping when needed
            - Start your response with "Expression: " followed by the expression
            
            Examples of correct format:
            Expression: x AND y
            Expression: (x OR y) AND NOT x
            Expression: NOT (x AND y)
            Expression: NOR(x, y)
            
            DO NOT use formats like:
            - \(x \land y\)  (LaTeX)
            - `x AND y`  (markdown)
            - x ∧ y  (mathematical symbols)
            
```

### P1

```text
You are given partial observations of a Boolean function with variables: x, y

            Allowed operators: AND (conjunction), OR (disjunction)

            Observations (input -> output):
            {{OBSERVATIONS}}

            Prior expressions generated (avoid repeating any expression 
            that is equivalent under the rules below):
            {{PRIOR_EXPRESSIONS}}

            Task: Generate a single Boolean expression that is consistent with ALL observations.

            Requirements:
            1. Use ONLY the variables: x, y
            2. Use ONLY these operators: OR, AND
            3. The expression must match all given observations
            4. Structural uniqueness is judged by these rules:
            - Commutativity: reorderings are the SAME (e.g., x AND y = y AND x).
        - Idempotence: duplicates of the same input under AND/OR collapse (e.g., x AND x = x; x OR x = x).
        - Associativity flattening: cascades of the SAME operator are the SAME regardless of parentheses (e.g., (x AND y) AND x = x AND (y AND x) = x AND x AND y).
        - No distributivity, absorption, or De Morgan normalization: mixing operators keeps expressions DIFFERENT (e.g., (x AND y) OR x ≠ x AND (y OR x)).
            5. Expression depth should be at most 2 levels of nesting
            6. Do not use boolean constants True or False anywhere in the expression.

            Output format: 
            - Return ONLY the Boolean expression on a single line
            - Use plain text format (no LaTeX, no markdown, no special formatting)
            - Use uppercase for operators. 
            - Use lowercase for variables: x, y
            - Use parentheses for grouping when needed
            - Start your response with "Expression: " followed by the expression
            
            Examples of correct format:
            Expression: x AND y
            Expression: x OR y
            Expression: x AND (x OR y)
            Expression: y OR (x AND y)
            
            DO NOT use formats like:
            - \(x \land y\)  (LaTeX)
            - `x AND y`  (markdown)
            - x ∧ y  (mathematical symbols)
            
```

### P2

```text
You are given partial observations of a Boolean function with variables: x, y

            Allowed operators: AND (conjunction), OR (disjunction)

            Observations (input -> output):
            {{OBSERVATIONS}}

            Prior expressions generated (avoid repeating any expression 
            that is equivalent under the rules below):
            {{PRIOR_EXPRESSIONS}}

            Task: Generate a single Boolean expression that is consistent with ALL observations.

            Requirements:
            1. Use ONLY the variables: x, y
            2. Use ONLY these operators: OR, AND
            3. The expression must match all given observations
            4. Structural uniqueness is judged by these rules:
            - Commutativity: reorderings are the SAME (e.g., x AND y = y AND x).
        - Idempotence: duplicates of the same input under AND/OR collapse (e.g., x AND x = x; x OR x = x).
        - Associativity flattening: cascades of the SAME operator are the SAME regardless of parentheses (e.g., (x AND y) AND x = x AND (y AND x) = x AND x AND y).
        - No distributivity, absorption, or De Morgan normalization: mixing operators keeps expressions DIFFERENT (e.g., (x AND y) OR x ≠ x AND (y OR x)).
            5. Expression depth should be at most 2 levels of nesting
            6. Do not use boolean constants True or False anywhere in the expression.

            Output format: 
            - For this basic grammar, AND and OR are the only allowed operators.
            - The examples illustrate only the syntax and output format. They do not represent the correct answers for the current observation group.
            - Your expression must match every observation for the current group.
            - Return ONLY the Boolean expression on a single line
            - Use plain text format (no LaTeX, no markdown, no special formatting)
            - Use uppercase for operators. 
            - Use lowercase for variables: x, y
            - Use parentheses for grouping when needed
            - Start your response with "Expression: " followed by the expression
            
            Examples of correct format:
            Expression: x AND y
            Expression: x OR y
            Expression: x AND (x OR y)
            Expression: y OR (x AND y)
            
            Additional format examples:
            Expression: x AND y
            Expression: x OR y
            DO NOT use formats like:
            - \(x \land y\)  (LaTeX)
            - `x AND y`  (markdown)
            - x ∧ y  (mathematical symbols)
            
```
