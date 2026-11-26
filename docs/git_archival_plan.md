# Git 版本归档准备

## 当前工作区

第三轮开始时分支为 main，HEAD 为 95f2447，远程为 origin，已跟踪 diff 和暂存 diff 为空。依第三轮授权，用同一个 HEAD 创建 codex/p3-archive-review，按下表列出的路径准备本地暂存。独立 HypoSpace checkout 保持固定提交 c69e9318577b34b5b896996571aefd4ba6053f58 及干净工作树。第三轮准备阶段仅暂存。审核通过后，归档内容已在本地提交，远程发布仍待审。

.gitignore 覆盖 data、results、.venv、环境文件、缓存、checkpoints 和常见模型扩展名。被忽略的文件仍需进入本地受限归档的校验清单。对已有代码、Prompt、文档、测试及 Git 文件的常见密钥格式扫描没有命中。答案相关搜索命中方案文档的防泄漏字段和明确构造的合成测试夹具。逐处核对后，暂存范围未发现实验真实答案标签。已有可审查文件最大约 56 KB。P3 的原始日志约 1.34 MB，体积不构成单独公开的理由。 [忽略规则](../.gitignore)

## 归档文件清单

| 归档决定 | 文件或路径 |
| --- | --- |
| 保留现有 Git 历史 | .gitignore、.python-version、AGENTS.md、PROJECT_BRIEF.md、README.md、configs/boolean_smoke.json、pyproject.toml、uv.lock |
| 固定已冻结输入的字节 | .gitattributes 对 11 个 P3 Freeze 路径及 D2 的 P0 禁用换行转换。scripts/run_boolean_smoke.py 的工作区字节仍是 P3 冻结的 CRLF，旧 Git blob 为 LF，本轮暂存保留冻结的 CRLF，不改变源代码文本。 |
| 审核后逐项纳入 Git | scripts/ollama_process.py、scripts/p3_collection_seal.py、scripts/p3_preflight.py、scripts/prepare_d2_prompt.py、scripts/prepare_p3_validation.py、scripts/run_d2_prompt.py、scripts/score_d2_prompt.py、scripts/score_p3_prompt.py |
| 审核后逐项纳入 Git | tests/run_offline.py、tests/test_d2_boolean_preflight.py、tests/test_d2_prompt_collection.py、tests/test_p3_validation_preflight.py |
| 审核后逐项纳入 Git | prompts/d2/P0.txt、P1.txt、P2.txt、P3-draft.txt、README.md（均位于 prompts/d2） |
| 暂存文档与公开实验摘要 | docs/algorithm_design_v2.md、docs/d2_prompt_report.md、docs/d2_result_analysis.md、docs/p3_validation_plan.md、docs/p3_deep_analysis.md、docs/algorithm_bottleneck_analysis.md、docs/git_archival_plan.md、docs/next_experiment_proposals.md、docs/independent_dataset_feasibility.md。公开 P3 汇总、阶段统计和配对指标可取自 p3_deep_analysis.md。 |
| 保存在访问受限的原件档案 | results/d1 的两次正式 smoke 目录、results/d2/prompt-20261126、results/p3/controlled-validation-20261126-210216-final、D1 数据集和运行环境记录。保留原始字节、相对路径和 SHA-256。 |
| 单独决定公开 | P3 requests.jsonl、official-results.json、group-seed-metrics.csv、paired-group-seed-comparison.csv、原始运行日志、Freeze 包及相关复现数据。 |

原始请求包含完整 Prompt、公开观测、历史、模型输出和原始 HTTP 正文。官方 JSON 有答案数量与逐轨迹候选证据。Preflight 和 launch verification 含本机路径及运行元数据。自动扫描不能排除私有路径、标识符或逐样本信息。发布前逐文件查阅，公开汇总优先使用文档。若批准公开原始证据，使用经过审查的独立副本及公开副本哈希。私有原件及其封存收据保持原字节，公开摘要不作为官方离线回放输入。

## 第三轮本地暂存与下一步审查

1. 对照上表重查已跟踪 diff、未跟踪列表、源码是否匹配 P3 Freeze、密钥与真实标签风险。固定源码时使用 P3 Freeze 中的代码、Prompt 和依赖哈希；后续算法变更另起提交。
2. 保留私有证据目录和校验记录，用只读校验比对副本。P3 原始请求 SHA-256 为 9cb5df616efbb254ea77a2a6787ff773a35177563cd4c3041de548f98d239cd0。对应 freeze.json、online_manifest.json、preflight.json 的 SHA-256 分别为 92add5900dd6024cac4cb7d89ecfc2fe818c92c48ce3b89b7a4ed1ae62186ae0、8bbbc0b0f335100822958fd522f45aa35bbf3531b4974aa3a6409bbba50b2c62、ba4c26e63b425c538cb1af0f38d3391721290ddbdde27aa64fb5f7b1f182cee5。selection_manifest.json 为 1ce33b70e408ac47e724eccd946c8142047a323bebb441554ada9e788d53942b。 封存收据（私有证据 `results/p3/controlled-validation-20261126-210216-final/collection_seal.json`）
3. 在已授权的本地归档分支按明确路径暂存，复查完整 staged diff、路径清单及 index blob 的字节哈希。归档分支以 95f2447 为祖先。审核后建立本地归档提交，公开文档修订另立提交，Push 仍待审核。公开仓库的复现说明需标明哪些证据要从受限档案获取，因为现有准备流程依赖精确的 D2 split 及数据集字节。 完整暂存差异（私有证据 `results/p3-third-review-20261126/staged.diff`） · 暂存文件清单（私有证据 `results/p3-third-review-20261126/staged-files.txt`） · 字节及敏感字段复核（私有证据 `results/p3-third-review-20261126/hash-and-sensitivity-verification.log`）

完整 whitespace 检查标记了冻结 Prompt 和旧报告中原样引用的 Prompt 缩进及行尾。保留这些字节，另对其余暂存路径运行 whitespace 检查。 检查明细（私有证据 `results/p3-third-review-20261126/staged-whitespace-check.log`）
