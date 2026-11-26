# D2 研究与技术方案 · FSCB-Hypo

**复审稿｜2026-11-26**。优先解决 Boolean `basic` 的生成合法性，再验证结构反馈能否在相同查询预算下提高官方 Recovery Rate（RR）。D2 Prompt 对照已完成。实测指标、放行判断与复现命令见 [D2 实验报告](d2_prompt_report.md)。

## 1. D1 失败分析

核对了 [AGENTS.md](../AGENTS.md)、[PROJECT_BRIEF.md](../PROJECT_BRIEF.md)、[README.md](../README.md)、[runner](../scripts/run_boolean_smoke.py)、[配置](../configs/boolean_smoke.json) 和 `results/d1/` 两个运行目录的请求、评分、校验、环境和 checkpoint 记录。项目 `main` 在 `95f2447`，上游独立 checkout 在 `c69e9318577b34b5b896996571aefd4ba6053f58`。`.venv` 为 Python 3.11.15。D1 记录的 Ollama 为 `0.40.14`，模型是 `qwen2.5:3b` Q4_K_M，摘要前缀 `357c53fb659c`，当时加载在 RTX 4060 Laptop GPU 上。数据元信息为 35 组，按 1、2、3、4 条观测分别为 6、13、12、4 组。最初的数据核验断言误把 `NOT` 当作 basic 操作符，随后已按源码纠正。

| 记录 | 实际 HTTP 请求 | 官方 parse / in-space | 官方 VR / NR / RR | 解释 |
| --- | ---: | --- | --- | --- |
| `smoke-20261125-173531` | 15 | 0 / 0 | 0 / 0 / 0 | 三个槽位各走满五次，日志中 15 次均 HTTP 200、都用 seed 42、原始文本相同且可被官方提取。旧语法保护拒绝含 `NOT` 的输出，Evaluator 收到空文本并重试。|
| `smoke-retry-20261125-174050` | 3 | 1 / 0 | 0 / 0 / 0 | seed 42、43、44，三条都为单行 `Expression:`、`done_reason=stop`、输出 22、18、14 tokens，且通过当前语法保护。三个 AST 都含禁用运算，其中第一条实际 AST 深度为 3。只读检查显示三条在可见观测行上的预测都是 `[1,1,1]`，观测为 `[0,0,1]`。|
| D1 手工正对照 | 0 | 1 / 1 | 1 / 0.5 / 0.5 | `x AND y` 与 `y AND x` 计为两次有效、一个机械键、一次召回。这是离线评分夹具。|

两次 smoke 质量门槛均以状态 1 退出。第二次日志的 `error_summary.total_errors=0` 表示请求处理正常，仍无有效候选。用第二次日志的三条原始文本在内存中重放官方 Evaluator，五项比例与保存结果一致。实证来源为 `results/d1/smoke-20261125-173531/{requests.jsonl,result.json}` 和 `results/d1/smoke-retry-20261125-174050/{requests.jsonl,result.json,manual-positive-control.json}`。

### 官方 Prompt 和 NOT 倾向

`create_prompt()` 的允许列表和 “Use ONLY” 约束正确读取了数据元信息，但靠后的四个正面格式示例中，两个使用 `NOT`，一个使用 `NOR`。结构规则的另一处例子还使用了变量 `z`。`all_hypotheses` 会把已提取的越界表达式放入下一次请求的 `Prior expressions generated`，第二次运行的实际请求确认了这种反馈。最可能的解释是 Qwen 模仿靠后的具体示例，并可能习惯用带否定的析取式拟合真值表。首个请求已出现 `NOT`，后续历史可能放大倾向。样本仅一组，示例模仿、模型先验及历史放大的因果贡献仍待对照验证。 [官方 Prompt 源码](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_benchmark.py#L79-L178)

### Parser、空间检查、Validator 与评分

| 层级 | 固定版本的实际处理 |
| --- | --- |
| `parse_llm_response` | 优先取含 `Expression:` 或 `expression:` 的首条非空表达式，然后尝试其他非空行。会清理 Markdown、LaTeX 与常见符号。提取成功只表示拿到候选文本。|
| `_in_space` | 用 `BooleanExpression` 解析 SymPy AST，检查变量、运算符、布尔常量和真实 AST 深度。`Not(Or(...))` 被识别成 NOR，其他 `Not` 按 NOT 检查。此方法独立于观测一致性。|
| `validate_expression` | 另行构造表达式，检查空间条件，再用表达式自身算出的真值表逐条匹配输入观测。返回 `(is_valid, truth_table)`。在线沿用这个判定并要求 `_in_space` 通过后才可入覆盖档案。两函数实现各自独立，若某边界输入判定不一致，照录并按原 Evaluator 产出官方分数。|
| `get_expression_mechanistic_key` | 用数据集的交换律、幂等和同算符结合折叠配置得到结构键。生成的完整真值表、`BooleanExpression.__eq__` 与用于显示的 CNF 均不参与官方机械去重。|
| `evaluate_single_observation_set` | 以预算槽位为分母。默认每槽最多五次，遇请求错误或提取失败会重试，提取到候选即结束该槽重试，后续发现候选越界或不一致也不会补抽。官方 NR 统计全部空间内候选的不同键，含与观测不一致者。官方 RR 在有效去重候选中匹配答案键。|

[官方 Parser、Validator 和机械键入口](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_benchmark.py#L180-L361) · [官方 Evaluator 与指标](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_benchmark.py#L422-L590)

### Ollama Adapter

D1 的 `OllamaSmokeLLM` 实现上游 `LLMInterface`，向 `/api/generate` 直接传入 `prompt`、`model`、`stream=false` 与 `options`。第二次请求日志确认 `temperature=0.6`、`seed=42/43/44`、`num_predict=128`、`num_ctx=4096` 均进入请求体。模型使用 Ollama 默认模板，D1 保留了默认的 `top_p`、`top_k`、重复惩罚、`stop` 和 `format`。D1 只验证了 0.6 的实际请求。D1 Adapter 在提取或 Guard 失败时会向 Evaluator 交付空字符串，该行为仅用于解释 D1 历史。D2 划分为原文归档、独立 Guard 诊断、原文官方回放三个步骤，详见下节。 [现有 D1 Adapter](../scripts/run_boolean_smoke.py#L95) · [Ollama Generate API](https://docs.ollama.com/api/generate)

## 2. D2 Prompt 对照协议

| 版本 | 变更 |
| --- | --- |
| P0 | 原样保留上游 `create_prompt()`，固定上游版本及 `PYTHONHASHSEED=0`。|
| P1 | 保留主体和原有四个正面示例的位置。将三条含 NOT/NOR 的示例分别替换为 `x OR y`、`x AND (x OR y)`、`y OR (x AND y)`。将含 `z` 的结构示例替换为 `(x AND y) OR x ≠ x AND (y OR x)`。原有约束原样保留。|
| P2 | 继承 P1，在输出说明附近强调只允许 AND/OR，追加 `Expression: x AND y` 与 `Expression: x OR y`。明确两式只展示语法和输出格式，不代表当前组的正确答案。生成结果须匹配当前组全部观测。|

- 完整模板、SHA-256 和逐行 diff 已冻结在 [Prompt 版本目录](../prompts/d2/README.md)。P0 → P1 比较示例修复，P1 → P2 比较约束强调与加例的联合干预。
- **5 个独立开发组 × 4 请求 × 3 个种子 × 3 个版本，共 180 次计划请求。** 各版本复用同一组清单、组顺序、逐槽模型 seed、温度 0.6 和生成上限。以 `(seed, group_position, query_index)` 预先映射为 Ollama seed。每组对普通模型响应沿用官方的已提取候选历史规则，包含提取成功但空间越界的候选。Guard 结果单独记录。首槽为空历史，可单独估计直接 Prompt 效应。后续槽按各自轨迹估计累积反馈效应。记录候选含 `NOT/NOR` 的频率及各版示例机械键命中，尤其是首槽。这些事件均进入官方评分。
- 每槽一次实际生成。固定相同上下文、输出长度、模型 tag/digest、量化和模板，记录 Ollama 版本及模型默认采样参数。观察误差时保留原始 HTTP、输出文本、`done_reason`、tokens 和耗时。越界、不一致、空输出或不可解析均消耗预算。传输故障也记录已发出的槽位并标记为基础设施故障，该槽评分保留，策略不更新。若形成集中服务故障，暂停整个受影响的配对块并按预先约定重跑整块，原日志留存。Prompt 实验期间保持文本格式，不切换 JSON schema、约束采样或自动修复模型文本。
- 每次输出记录提取成功、Guard 诊断、空间内、空间内且观测一致、首次发现该组有效机械键五个标志，分母均为全部槽位。同时报告第一条有效候选和第一条有效新键出现的位置，未出现时记为 `>B`。分组、分种子报告，离线计算官方 VR/NR/RR。
- 候选放行门槛为开发集汇总 VR ≥ 25%，且至少 2 个种子各有 ≥ 4/5 组发现至少一个有效候选。在通过的 P1/P2 当中按“有效新键数 / 请求数”选版。差值 ≤ 5 个百分点时优先空间合规率，仍相同时优先较短的 P1。P0 始终报告为原始 Prompt 对照。若没有 P1/P2 达标，继续在开发组定位失效率最高的环节，冻结前用下一组开发数据验证改版。正式矩阵使用同一基础模板哈希和同一历史规则，只允许预注册实验臂追加相应提示。

### 官方回放与 Guard 隔离

1. 请求返回后，先按槽位封存原始 HTTP 响应、Ollama 的 `response` 字符串、实际发送 Prompt 及哈希。Guard 可以另外分析官方 Parser 提取的候选，并记录 verdict 和原因。生成质量失败由官方口径计分。
2. 离线 `Replay(LLMInterface)` 每槽直接返回封存的 `response` 字符串，用官方 `evaluate_single_observation_set(..., max_retries=1)`。读取记录后保留原文空白、格式与运算符。Guard verdict 不进入 Replay。只有确认缺少模型响应的传输错误使用上游错误标志，原 HTTP 记录保留。
3. 官方 Boolean 实现会把字符串交给 SymPy。真实模型输出的评分进程需能安全处理不可信文本。若真实安全问题导致某条原文无法处理，封存原文并暂停受影响的配对块，待安全评分条件满足后回放原文。该块在完成回放前没有完整的官方结果。预检只回放已审阅的 D1 和合成夹具。

| 指标 | 固定版本字段与含义 |
| --- | --- |
| Parse Success | `parse_success_rate`，官方 Parser 成功提取的槽位比例。|
| In-space Rate | `in_space_rate`，满足官方空间约束的候选次数除以全部查询槽位。|
| Official VR | `valid_rate`，满足空间约束及可见观测一致性的候选次数除以全部查询槽位。官方值由原 Evaluator 的 `validate_expression()` 路径产出。若以后发现空间检查与验证路径有边界差异，记录原输出和差异供复审。|
| Official NR | `novelty_rate`，空间内不同机械键数除以全部查询槽位，包含与观测不一致的空间内候选。|
| Official RR | `recovery_rate`，有效机械键匹配官方答案键后得到的召回比例。|

### 无模型调用预检结果

以下为该固定版本的实测。合成夹具沿用同一 D1 观测组，单个合法候选命中两个答案键中的一个。该答案集只用于离线评分。

| 夹具 | Parse Success | In-space Rate | Official VR | Official NR | Official RR |
| --- | ---: | ---: | ---: | ---: | ---: |
| D1 反例，封存原始 3 条输出 | 1 | 0 | 0 | 0 | 0 |
| D1 手工正对照，两个等价候选 | 1 | 1 | 1 | 0.5 | 0.5 |
| 合法 `x AND y` | 1 | 1 | 1 | 1 | 0.5 |
| NOT 越界 `NOT x` | 1 | 0 | 0 | 0 | 0 |
| AST 深度 3 的 `x OR (x AND (x OR y))` | 1 | 0 | 0 | 0 | 0 |
| 观测不一致 `x OR y` | 1 | 1 | 0 | 1 | 0 |
| 无法提取 `# no expression` | 0 | 0 | 0 | 0 | 0 |
| 同一机械键 `y AND x`、`x AND y` | 1 | 1 | 1 | 0.5 | 0.5 |
| Guard 拒绝、原文可提取的 `Expression: z` | 1 | 0 | 0 | 0 | 0 |

`Expression: z` 是已审阅的合成夹具。D1 Guard 返回拒绝，官方仍收到封存文本并将 `z` 计入 `all_hypotheses`，因此 Parse Success 为 1。

## 3. 官方 AST、Grammar 与特征选择

生成器的 basic 文法可写为 `E₀ ::= x | y`，`E_d ::= E_(d−1) | (E_(d−1) AND E_(d−1)) | (E_(d−1) OR E_(d−1))`，这里 `d≤2`。生成器先按二元构造枚举，官方判定实际用 SymPy AST。`_parse_formula()` 将关键字转为 SymPy 的布尔运算符，并用 `evaluate=False` 解析。某些 SymPy 构造仍会合并同类节点或幂等输入。叶深度为 0，内部节点深度为 `1+max(children)`。文本括号深度与 `_get_ast_depth()` 有不同含义。规范化机械键常为 `('VAR', name)`、`('AND', children)` 或 `('OR', children)`，`children` 是排序后的子键元组，可能有两个以上子项。实际交换次序和 `x AND x` 的重复可折叠，吸收形如 `x OR (x AND y)` 与 `x` 则保留不同机械键。本轮只读使用官方生成器按公开语法统计描述符，共 10 个机械键，与 D1 元信息一致。基础配置只有 4 个真值函数。 [AST 和机械键](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_dataset.py#L42-L184) · [官方文法与枚举](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_dataset.py#L214-L221) · [生成器](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_dataset.py#L280-L333)

| 特征 | 可复现定义 | D2 用途 |
| --- | --- | --- |
| Operator Count | 机械键树的内部算符节点数 | 在 10 个官方生成键上严格等于结构深度，保留为诊断。另行记录 AST arity 时，区分三输入门与两个算符节点。|
| Expression Depth | 机械键树的最长根到叶路径 | 与官方原 AST 深度分别记录。用于桶的叶 / 单门 / 混合门结构划分。|
| Root Operator | 机械键首项，叶单列 | 区分根为 AND 和 OR 的单门与混合结构。|
| Variable Support | 机械键所含变量集合 | 仅作为诊断。支持集大小在这 10 个键上等价于“是否为叶”。记录 `{x}`、`{y}`、`{x,y}` 时应称为语法支持，吸收结构的功能依赖可能较小。|
| Tree Structure | 带节点标签的规范树，诊断可抹去叶名称保留 arity | 完整树键用于上游去重。用于奖励的是粗粒度树形，避免每个完整机械键都变成独立桶并退化成首次发现奖励。|

### 官方 mechanistic key 映射

| 由官方生成器枚举的 key | 结构桶 |
| --- | --- |
| `('VAR', 'x')` | leaf |
| `('VAR', 'y')` | leaf |
| `('AND', (('VAR', 'x'), ('VAR', 'y')))` | AND-only |
| `('OR', (('VAR', 'x'), ('VAR', 'y')))` | OR-only |
| `('AND', (('OR', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'x')))` | AND-root mixed |
| `('AND', (('OR', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'y')))` | AND-root mixed |
| `('AND', (('OR', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'x'), ('VAR', 'y')))` | AND-root mixed |
| `('OR', (('AND', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'x')))` | OR-root mixed |
| `('OR', (('AND', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'y')))` | OR-root mixed |
| `('OR', (('AND', (('VAR', 'x'), ('VAR', 'y'))), ('VAR', 'x'), ('VAR', 'y')))` | OR-root mixed |

按表中桶顺序，固定容量 `K_f` 为 **2、1、1、3、3**。单元测试用官方 `generate_all_expressions()` 按公开 Grammar 重新枚举，再用官方机械键方法核验每种表示。表中每个键恰有一行，集合与枚举完全一致。容量表示公开 Grammar 下每桶的机械键数，对每组统一，即使当前观测只允许其中一部分。在线只用这五个容量常量。遇到表外的官方有效键时保存官方评分，标记映射异常，并在正式冻结前核对来源。

## 4. FSCB-Hypo 候选闭环

每组持有候选历史、有效机械键集合 `S`、五个结构桶的档案计数 `c`，以及每个桶的连续未命中计数 `m`。`S` 和 `c` 仅接纳空间内、经官方验证且在本组首次出现的键。组间清空这些数据。

`Coverage_f = c_f / K_f`。若已发现至少一个有效键，候选前沿是 `{f | c_f < K_f 且 m_f < 2}`，在其中选择覆盖率最低的桶。用该组预定的种子化顺序打破并列。档案为空、前沿为空或附加段无法满足 token 上限时，只开放 Standard 动作。已完全覆盖的桶退出本组目标选择。

```text
每个 observation group 开始：S = ∅，所有 c_f 和 m_f = 0
每槽开始：
    eligible = {f | c_f < K_f and m_f < 2} if S 非空 else ∅
    target = eligible 中使 c_f / K_f 最小的桶，按组内固定顺序打破并列
    根据 target 和提示长度确定可用动作，LinUCB 选择动作并发出一次请求
    封存原文，记录实发模式、目标桶及决策前 Coverage
    若发生 HTTP 或基础设施错误：记录槽位，继续下一槽
    用官方 API 判定有效性和机械键，用更新前 c 计算奖励
    若是本组有效新键：更新 S 和实得桶的 c
    若实际发出 Frontier 提示，且本槽有可评分的模型响应：
        若是目标桶的有效新键：m_target = 0
        否则：m_target += 1
    用决策前 context 和实际发送的 arm 更新 LinUCB
```

`m_f` 记录向该桶发送的真实 Frontier 提示的连续未命中。重复键也计一次未命中。在其他桶上的请求不改变本桶计数。具体处理如下。

| 槽位结果 | 未命中计数 |
| --- | --- |
| 模型响应不可解析 | 目标桶加 1 |
| 表达式超出空间，或与任意观测不一致 | 目标桶加 1 |
| 得到有效新键，但属于其他桶 | 目标桶加 1，实际桶的边际奖励正常计算 |
| HTTP 错误、超时或其他基础设施故障 | 保持原计数 |
| Standard 及无前沿消融的中性提醒 | 保持原计数 |
| 得到目标桶的有效新键 | 目标桶清零 |

连续两次未命中的桶在本组剩余查询中暂缓提示，下组重新开放。未命中只用于节流，无法判定是否存在满足当前观测的合法候选。运行中若官方 Validator 接受而机械键缺失，记为集成异常，保留官方评分并跳过策略和档案更新。

Frontier 动作在基础 Prompt 输出说明前最多增加一行，例如 `If compatible with every observation, consider an underexplored form with an AND at the root and both gate types. Any legal matching expression is welcome.` 其余桶用固定短语。附加段上限为 48 输入 tokens，实际请求仍须满足原有约束。提示中不使用具体候选、精确算符数、答案键、按组容量或官方复杂度比例。每槽记录实际发送的模式、目标桶、决策前 `c_f/K_f`、命中状态（命中、未命中或不适用）、实得桶及计数变化。

## 5. 边际结构覆盖奖励

这是基于结构桶计数的边际新颖性奖励。对 `g(c)=1−exp(−c/κ)`，将单次增量 `(1−exp(−1/κ)) exp(−c/κ)` 除以空桶的最大增量，得到 `δ_t = exp(−c_f(t)/κ) ∈ [0,1]`。每个有效新键只在一个桶获得一次边际奖励。相同的先前发现数得到相同增量，与桶容量无关。容量差异影响各桶可能出现几次递减：单键桶至多奖励一次，多键桶还会依次奖励后续新键。不同桶在相同覆盖比例下可能得到不同奖励。用 `c_f/K_f` 平衡提示目标的选择，奖励保留原始计数定义。

| 槽结果 | 基础奖励 `r₀` | FSCB 候选 `r` |
| --- | ---: | ---: |
| 有效且首次发现机械键 | `1` | `(1−β) + βδ_t` |
| 有效但机械键重复 | `0.2` | `0.2`，桶计数不变 |
| 官方不可提取、越界或观测不一致 | `−0.5` | `−0.5`，桶计数不变 |
| HTTP、超时等基础设施错误 | 无模型反馈 | 跳过策略参数更新，槽位仍记账 |

固定 `κ=1`、`β=0.25`，同桶首、次、第三个不同键约为 `1.000 / 0.842 / 0.784`。奖励范围为 `[-0.5,1]`，有效新键始终比重复键奖励高。`β=0` 恢复 Basic ACB 奖励。Guard verdict 单独记录，发现有效新键时按实际产出的桶计奖。奖励仅由在线验证、组内去重与结构桶计数决定。

## 6. LinUCB 的 State、Action、Reward、Update

| 项目 | 候选规范 |
| --- | --- |
| State | `x_t = [1, n_obs/4, (B−t)/B, recent_valid_rate, recent_unique_valid_rate, occupied_bins/5]`，`t` 为已尝试槽数，最近窗口 `W=4`，空历史两个比例均填 0。有服务错误的槽位不加入模型产出窗口。Basic ACB 保留前五维及原奖励。状态无组 ID 编码、完整真值表、兼容候选数或 GT 统计。|
| Action | Standard × `{0.2,0.6,1.0}` 和 Frontier × `{0.2,0.6,1.0}`。温度与 Prompt 模式共同构成 arm ID。只对可用臂打分。每步记录可用 arm、最终发出 payload、Frontier 目标桶、其决策前覆盖率和命中情况。目标桶信息仅进日志，不增加 arm 或 Context 维度。|
| Reward | 使用该 action 对应的实际生成结果和决策前档案，按 §5 计算。温度、Prompt 模式和随机种子不直接进入 reward 函数。|
| Update | 每个 arm 从 `A_a=λI, b_a=0` 起步，`θ_a=solve(A_a,b_a)`。选取 `xᵀθ_a + α√(xᵀ solve(A_a,x))` 最大的可用臂，更新 `A_a ← A_a + xxᵀ`，`b_a ← b_a + r x`。仅更新实际发送的 arm，复用保存的决策前 `x`。每个 `(run, group, slot)` 只提交一次更新，恢复时按槽位日志重建或核对策略状态。|

初始用 `λ=1, α=0.5` 和种子化平局规则。默认以同一方法和种子为单位隔离 `A,b` 与各臂反馈数，并在组间共享这些参数。若当前有反馈数不足 2 的可用臂，优先选择反馈最少者，按种子打破并列。否则用 UCB。尚不可用的臂首次开放后仍可获得预热机会。对每个 Frontier 温度，Disjoint LinUCB 学习的是该动作在当前 Context 下、汇总其实际提示过的各桶后的预期收益。各桶自身的效果只从曝光和命中日志诊断。跨 10–15 组有 100–180 个总槽位，六臂实际分配可能不均。Pilot 按臂报告可用机会及反馈数，若预热不足则复审前沿激活条件和估计稳定性。维持六臂、六维 Context。

跨组在线协议按固定的 seed-specific 组顺序串行。每个 `(方法,种子)` 在正式轨迹第一组从头初始化，开发或 pilot 的模型权重和结构档案均不进入正式轮次。策略可从先前正式组的可见反馈学习，组内历史和状态独立重置。同种子的各方法经历相同组顺序，另记录不同种子的顺序敏感性。动作会改变后续档案与奖励，Disjoint LinUCB 在此作为低成本逐步决策策略，经典独立线性 payoff 假设及长程最优性需谨慎解释。 [Li et al., LinUCB](https://arxiv.org/abs/1003.0146)

## 7. 研究增量与可验证性

| 研究 | 已有机制及与本方案的关系 |
| --- | --- |
| HypoSpace Complexity-Stratified Decoding | 文章 §6.5 指定每次生成精确的复杂度层，对 Boolean 以操作符数量为尺度，并按答案集复杂度分布分预算。结构提示这一大方向已存在。FSCB 的提示来自在线有效新键的档案，目标是粗结构且可选，预算由在线策略分配。可观测的区别是即使同一难度组，提示目标也会随先前发现改变。 [论文 §6.5](https://arxiv.org/html/2510.15614v3#S6.SS5) |
| Learning Adaptive LLM Decoding（2026） | 冻结 LLM，在序列级用提示嵌入与并行预算选解码配置，在 token 级基于模型内部状态做 POMDP 控制，两者采用 REINFORCE 训练 adapter。已有研究覆盖“冻结模型＋上下文 bandit 选解码”。我们的上下文来自同组发现历史和剩余跨请求预算，反馈来自验证和结构覆盖，动作包含 Prompt 干预。 [Su et al. §2](https://arxiv.org/html/2603.09065v2#S2) |
| IPS-GRPO | 对终结 outcome 的经验概率做逆概率缩放，改变 GRPO 的策略梯度更新以缓解 mode collapse。其 HypoSpace 实验按答案集匹配给验证奖励。FSCB 冻结生成模型，使用已验证新键所在结构桶的计数增量，学习抽样动作，未估计 outcome 概率。 [Sinha et al. §4 和附录 B.1](https://arxiv.org/html/2601.21669v1) |
| MS-TTRL（2026） | 将 correctness 与稀有 outcome 分开，用 outcome 级自验证、三态 cache 和置信门控的 IPS 做测试时强化学习。与我们共享“先判断有效性，再促进多样性”原则。FSCB 直接使用确定性观测验证，结构桶统计不同有效机械键，并通过 prompt 和奖励反馈给轻量控制器。 [Zhang et al. §4](https://arxiv.org/html/2610.32234v1#S4) |

基础工具是 LinUCB、确定性校验、结构特征、基于计数的探索与覆盖档案。MAP-Elites 已普及描述符档案，Monte Carlo Elites 已将质量多样性档案同 UCB 结合，计数衰减奖励也有成熟文献。改变温度或把这些技术直接并列的贡献有限。项目层面可以提出一个具体、可解释的候选方法。**同一个在线有效结构档案向冻结 LLM 的提示和向 contextual bandit 的首次发现奖励分别提供反馈，以改善预算受限的集合召回。** 预注册可检验预期为 Frontier 动作改变目标桶命中和首次覆盖时间，边际奖励在同样动作机会下改变重复结构的投入，并且 Full 提高官方 RR。若只提高结构诊断、仅优于固定温度而无法胜过随机或 Basic ACB，或仅一项消融出现差异，应按对应证据缩小结论。当前证据支持研究可行性，尚无方法增益结果或优先权结论。课程是否认可推理时更新的小型控制器由课程方确认。 [MAP-Elites](https://arxiv.org/abs/1504.04909) · [Monte Carlo Elites](https://arxiv.org/abs/2104.08781) · [计数探索](https://arxiv.org/abs/1606.01868)

## 8. 正式矩阵、评分与数据隔离

| 条件 | Prompt 模式与策略 | 奖励 | 主要比较 |
| --- | --- | --- | --- |
| Fixed 0.2 | Standard，固定 0.2 | 无更新 | 低温基线 |
| Fixed 0.6 | Standard，固定 0.6 | 无更新 | 与 D1 温度相同的基线 |
| Fixed 1.0 | Standard，固定 1.0 | 无更新 | 高温基线 |
| Random Strategy | 按自身轨迹采用与 Full 同一套动作可用性和预热规则，其他槽使用独立 RNG 从可用臂均匀抽样 | 只记日志 | 控制拥有相同 Prompt 与温度选择的收益 |
| Basic ACB-Hypo | Standard × 三温度，五维 context，Disjoint LinUCB | 原始 `r₀` | 保留初始研究方向作为独立基线 |
| Full FSCB-Hypo | Standard / Frontier × 三温度，六维 context，Disjoint LinUCB | `β=0.25` | 完整方法 |
| Without Marginal Structural Coverage Reward | 与 Full 同动作、前沿模板、context 与探索规则 | `β=0` | 估计额外结构奖励的贡献 |
| Without Frontier-guided Prompt | 保持六个 arm ID、同一前沿候选计算与预热规则。原 Frontier ID 发送同一基础 Prompt 加约等长的中性提醒，照常记实际发现并更新覆盖 | `β=0.25` | 估计动态结构提示机制的贡献，同温的两个臂仍独立更新 |

中性提醒只重申观测及单行格式，例如 “Continue to follow all observations and the single-line response format. Consider the permitted variables and operators, and return one expression in the requested format.”。开发阶段检查它的长度与前沿句近似。中性提醒没有向模型发送结构目标，因此只记计划桶及覆盖率，实际命中为不适用，`m_f` 始终为零。两种条件的实际可用臂机会可能因此不同，须分别报告曝光和覆盖，消融估计动态提示机制的整体影响。Basic ACB 保留三个纯温度臂。现有两项消融用于各自的边际比较，完整二因素交互需要额外的“双关闭”条件。

### 公平性与记录

- 三个阶段预先按**可见观测条数**分层，保存不重叠的组 ID manifest，排除已知的 D1 组。建议 5 个 Prompt 开发组、另 5 个 pilot 组、15 个冻结正式组。用可见输入输出内容构造排序后的指纹检查跨 split 重复，留出额外组应对门槛失败。基本数据集中不同观测组仍会共享潜在布尔函数，正式结果体现不同观测任务的迁移，超出此数据集或迁移到新公式族需要独立证据。
- 统一基础 Prompt、模板和渲染哈希、历史规则、生成上限、验证流程、槽位与 seed 映射。历史取最近最多四条安全可纳入的已提取候选，保留组内顺序，超出上下文时按最旧整行裁减。Prompt 实验 `B=4` 时记录数上限不介入。所有方法固定 `B`，每个槽位都记账，包含预热。温度策略与随机策略使用独立 RNG 流，模型 seed 按配对规则独立预生成。每个条件报告动作曝光、选择率、输入与输出 tokens、缓存和耗时。前沿额外文本有 48 token 上限，主效应按同一查询与输出上限报告，并给出累计 token 成本或共同 token 区间的 RR 曲线。
- main run 的控制进程只读取由公开变量、操作符、深度、机械规则和组内观测组成的白名单文件。官方方法需要 benchmark 对象时，构造仅含上述元数据与剥离标签的 observation sets 的临时输入。隔离 `ground_truth_expressions`、兼容数量、答案复杂度比例与答案真值表。官方 Validator 返回的候选真值表也丢弃，不进入 Controller。封存输出、槽位与模型响应后，由离线 scorer 单独加载原始答案。在线机制的跨组参数更新只依赖先前组的在线反馈。
- 每槽记录配置版本、模型摘要、封存原始返回、Guard verdict、提示与 token、预决策 context、动作理由、阶段标签、奖励与更新。离线评分按 §2 原文回放，回放的 Evaluator 会在内部构造 Prompt，实际生成时的 Prompt 以已封存请求为准。评分文件与在线输入分开。

### 指标、规模与判定

- 每组按固定版本原样计算 `VR = 有效输出次数 / B`，`NR = 空间内不同机械键数 / B`，`RR = 命中的官方答案键条目数 / 官方答案键条目总数`。RR 为唯一主指标。官方 NR 可在 VR 为 0 时大于 0。在线有效新键率、重复率、首个有效发现位置与官方指标分别记录。 [固定版本公式](https://github.com/CTT-Pavilion/_HypoSpace/blob/c69e9318577b34b5b896996571aefd4ba6053f58/boolean/boolean_benchmark.py#L520-L585)
- Structural Coverage 是组内已占用桶数及除以全局五桶后的比例，并报告目标桶命中率、有效首次发现的形状分布和 coverage-versus-query 曲线。该比例只描述公开文法的粗桶，某些组的合法形状天然达不到 5。无需答案键即可算出。辅助分析可记录每条生成预测是否匹配公开观测，越界候选仍按官方有效性处理。
- Pilot 用留出的 5 组 × 8 槽 × 1 组种子 × 8 条件，最多 **320** 次。门槛为请求与日志槽位一一对应、D1 正反夹具指标可复算、无标签流入请求或奖励、重复与无效更新正确、状态跨组重置符合预期，且每个前沿温度在 Full 可用时至少得到两次真实反馈。检查 Full 与 Random 的 Frontier 分支均按各自档案渲染目标，且观测到至少两个目标桶、至少一次目标桶有效新键命中，并完成 token 计量。若命中稀少，应把可利用的反馈量作为方案冻结的审查项。若随机波动导致未激活，先判定是 valid gate、臂曝光还是提示渲染问题。记录当时 GPU、热态中位及尾部时延。
- 正式首选 8 条件 × 15 组 × 12 槽 × 3 种子 = **4,320** 次。算力紧张时按 pilot 冻结 8 × 10 × 10 × 2 = **1,600** 次，预先确定统一的缩减方案和分层子集。按方法先算每种子组级宏平均，再报告跨种子均值与 SD，同时给出配对的组级 RR 差、种子方向和 RR 随查询变化。跨组在线学习引入串行依赖，组级差值可作描述，不能把单个请求或同一组的多种子当独立样本。2–3 个种子只支持有限的不确定性描述。优先检查 Full 对 Basic ACB、匹配随机及两项消融的差异，同时完整列出三条固定温度结果。若 Full 只提高结构覆盖，按诊断结果报告。

## 9. 剩余一周的顺序、验证和耗时

| 时间 | 优先级与工作 | 审查及验收 | 预计 |
| --- | --- | --- | --- |
| D2 审核后 | 优先修 Prompt 及严格每槽一次的实验 runner，并预注册分组、种子、P0/P1/P2 | 通过 payload、guard 和回放夹具，完成 180 次开发比较并冻结基础版 | 工程约 2–4 小时，另计模型运行 |
| D3 | 先接固定策略、配对随机和 Basic ACB，再加入结构桶、奖励、提示与 Full 策略、两个消融 | 用离线测试验证 commutativity、幂等、吸收不折叠、出界、误观测、动作归因、状态重置、独立离线评分 | 约 4–6 小时 |
| D4 | 运行 5 组 pilot、修复统计链路，冻结模式、报告与运行参数 | 通过 §8 pilot 门槛并确认显存与时间。若延迟或模型质量不合格，先复审范围 | 工程约 3–5 小时，另计模型运行 |
| D5 | 冻结矩阵正式生成、封存与离线评分 | 检查相同槽位数、组顺序、重放一致性与日志完整性 | 若 1–4 秒/槽，4,320 槽约 1.2–4.8 小时，1,600 槽约 0.4–1.8 小时 |
| D6 | 汇总组级 RR、VR、NR、两项消融、配对结果和结构诊断，整理复现配置和 README | 图表从封存结果生成，队友复查各项数字 | 约 4–6 小时 |
| D7 | 四人复核复现、研究结论和 10 分钟讲稿，录制与提交 | 数据、配置、代码和解说的一致性检查 | 约 2–3 小时，另计录制缓冲 |

D1 第二次三个热态请求约 0.24–0.30 秒，第一次冷请求约 5.88 秒。工时表的 1–4 秒/槽是留有余量的估算，正式计划以长历史的 pilot 实测和故障率更新。团队分工沿用 brief，A 负责官方校验和分组，B 负责模型和请求日志，C 负责控制器，D 负责配对分析和展示。建议实现放在独立的控制器/结构模块、实验 runner、配置和测试中，沿用现有项目入口布局，上游 checkout 维持独立。每个获批阶段记录改动文件、实现、测试、真实结果和遗留问题。

## 10. 风险与触发条件

| 风险 | 发现方式和处理 |
| --- | --- |
| 修 Prompt 后仍然越界，或只复述两个 P2 例子 | 提取、空间、观测和首次有效四层漏斗，以及首槽示例键命中。未过开发门槛继续在开发数据定位。|
| basic 太小，桶早饱和，或同一组只允许少数桶 | 报告实际有效桶分布和未命中率，匹配随机与 Basic ACB 作解释。降低规模结论。更大空间属于另行立项。|
| SymPy 解析形态与文本树或生成器预期不一致 | 钉死环境版本，测试多元门、幂等和超深表达式。用机械键提特征，保留 Validator 和官方回放判定。|
| 重试隐性增加算力或失败被分类为好输出 | 一槽一请求，核对实际 HTTP 次数、截断、错误类型、原文和评分输入。传输失败按预注册的整块故障规则处理。|
| 结构奖励改善桶覆盖但牺牲 RR 或 VR | 重复和无效奖励固定，首次发现奖励有下界，分析 Full 的两项消融和配对 RR。对指标分歧做实测解释。|
| 六臂被冷启动耗尽，或随机胜过学习策略 | 保留六臂，使用全局预热与跨组共享。报告每臂反馈、早期与后期表现，对曝光不足的臂限制结论。|
| 组端 GT 泄漏或跨条件共享学习状态 | 白名单输入、独立评分进程、状态按方法和种子隔离、日志重算。对禁用字段、动作错记和重复提交做回归测试。|
| 提示长度、模板默认值和运行速度变化 | 固定模型摘要、Ollama 版本和配置，保存实际请求、输入计量和加载情况。冻结前按 pilot 调整整张矩阵规模。|
| 课程贡献判断超出证据 | 使用“提出并评估一种结构反馈方法”的措辞，核对最接近的先例，向课程方确认冻结 LLM 的在线 bandit 方向，按实测决定结论强度。|

## 11. D2 Prompt 实验启动前检查

### 本轮无模型预检

- [x] 固定上游版本，核对官方 Prompt、Parser、Validator、Canonicalizer 和 Evaluator。
- [x] 保存 P0/P1/P2 完整模板、SHA-256 与 diff。核验 P0 等同官方渲染，核验 P1/P2 例子空间合法。
- [x] 用封存的 D1 反例、D1 手工正对照与合成夹具复验五项指标。核验 Guard 拒绝的夹具仍可由官方 Parser 从原文提取。
- [x] 重新枚举并核对十个机械键和五个桶容量。

运行命令（在仓库根目录）

```powershell
$env:PYTHONHASHSEED = '0'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_d2_boolean_preflight.py -v
```

该命令复用 README 的 D1 数据、记录与上游 checkout。合成和回放测试没有发出 Ollama 请求。

### 首次模型请求前的实际检查

- [x] 冻结三个不重叠的组清单，排除 D1，核对组 ID 与可见观测指纹。开发 5 组、Pilot 留出 5 组、正式留出 15 组。
- [x] 冻结三个随机种子及每组四槽的模型 seed 映射。统一 `temperature=0.6`、`num_predict=128`、`num_ctx=4096`、默认采样配置、模型摘要与模板。
- [x] 验证每槽一个 HTTP 请求、封存原文字节和提示哈希。Guard 单独记诊断。采集工作进程只接收去标签 manifest，载入后通过 Python 审计钩子禁用新文件读取并实际验证答案文件无法打开。另一个进程回放官方 Evaluator。
- [x] 核对 Python 3.11.15、Ollama 0.40.14、D1 模型摘要及 GPU 显存。冻结配对块规则，每组与每种子块累计至少两次基础设施错误时最多整块重跑一次，并保留全部原请求。
- [x] 复审批准后执行 180 次 D2 Prompt 开发请求。启动收据为 `results/d2/prompt-20261126/preflight.json`，实际评估结果见本轮报告。
