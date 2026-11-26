# Boolean basic Prompt 版本

[P0 原始模板](P0.txt) · [P1 示例修复](P1.txt) · [P2 明确约束与格式示例](P2.txt)

三个文件均为完整模板。`{{OBSERVATIONS}}` 替换为逐行拼接的官方 `BooleanObservation.to_string()`，`{{PRIOR_EXPRESSIONS}}` 替换为 `None` 或逐行拼接的 `Expression: <候选>`。两个插槽均只替换一次。渲染保留空白、缩进及上游末尾换行。P0 来源为固定版本 `c69e9318577b34b5b896996571aefd4ba6053f58` 的 `create_prompt()`。Preflight 在 `PYTHONHASHSEED=0` 下验证空历史和非空历史的 P0 渲染与官方逐字符相同。

## SHA-256

哈希输入是 UTF-8 编码、LF 换行的完整模板文本，包含原有空白。读取模板时统一为 LF，以兼容 Windows Git 的换行转换。实验日志同时记录模板哈希和每槽实际发送的渲染文本的 SHA-256。

| 版本 | 模板 SHA-256 |
| --- | --- |
| P0 | `2a4d50ce196564d295b6872355ccfbe9f376f0f2b7334e246ae0e0e6855b18dc` |
| P1 | `e6b0498c7866f667218ff16f163240d766439e052c84507e5a2a3911d71f36ab` |
| P2 | `5811d33e71fdafc0eaf59d10e6d50695480f8cef3959f3096072ba31cad6d265` |

## P0 → P1

四条正面示例的位置和数量保持一致。第一个示例 `x AND y` 保留。结构规则中两侧的新例都符合 basic 空间，且具有不同的官方机械键。

```diff
--- P0.txt
+++ P1.txt
@@ -22 +22 @@
-        - No distributivity, absorption, or De Morgan normalization: mixing operators keeps expressions DIFFERENT (e.g., (x AND y) OR z ≠ x AND (y OR z)).
+        - No distributivity, absorption, or De Morgan normalization: mixing operators keeps expressions DIFFERENT (e.g., (x AND y) OR x ≠ x AND (y OR x)).
@@ -36,3 +36,3 @@
-            Expression: (x OR y) AND NOT x
-            Expression: NOT (x AND y)
-            Expression: NOR(x, y)
+            Expression: x OR y
+            Expression: x AND (x OR y)
+            Expression: y OR (x AND y)
```

## P1 → P2

在输出说明附近强调仅可使用 AND/OR。附加的 `x AND y`、`x OR y` 重复展示语法和输出格式，当前组仍须根据所有观测求解。

```diff
--- P1.txt
+++ P2.txt
@@ -26,0 +27,3 @@
+            - For this basic grammar, AND and OR are the only allowed operators.
+            - The examples illustrate only the syntax and output format. They do not represent the correct answers for the current observation group.
+            - Your expression must match every observation for the current group.
@@ -39,0 +43,3 @@
+            Additional format examples:
+            Expression: x AND y
+            Expression: x OR y
```

P0 → P1 估计一组示例修复的效果。P1 → P2 估计明确约束和增补示例的联合效果，现有矩阵无法独立估计这两项改动。

## P3 · 已冻结并完成验证

[P3-draft.txt](P3-draft.txt) 是 P3 Controlled Validation 使用的独立冻结条件，沿用预注册文件名。180 次请求已完成，P3 未通过预注册的 Seed 稳健性门槛。 [实验结项](../../docs/p3_deep_analysis.md)

完整模板和两组精确差异见 [D2 离线分析第 6 节](../../docs/d2_result_analysis.md#6-p3-prompt-proposal)。

冻结模板 SHA-256（UTF-8/LF）为 `3dc9205f284eb45464dd7c70978bf006da3cdaad0ac5cf8b0da9aa206580b2a5`。
