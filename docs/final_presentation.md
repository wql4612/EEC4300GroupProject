# EEC4300 final presentation rehearsal

Ten minutes, four speakers. The baseline and proposed method have passed synthetic smoke tests. The deck labels the historical P3 results as measured and the exploratory comparison as unmeasured. Use the completed-results wording in the final segment only after checking a sealed official comparison. [Final editable PowerPoint](presentation/EEC4300-final.pptx)

| Time | Speaker | Screen |
| --- | --- | --- |
| 00:00–02:20 | Qiulin Wu | Task, benchmark metrics, D1–P3 evidence |
| 02:20–04:45 | Yujia Wang | Candidate history and online validation |
| 04:45–07:15 | Jiayi Zhou | ACB, FSCB, and the experimental controls |
| 07:15–10:00 | Wenkang Zhou | Official results or current evidence, limitations and reproduction |

## Qiulin Wu · Boolean mechanism recovery (2:20)

Show a synthetic Boolean observation, such as `(x=0, y=0) → 0`. Explain how several formulas can agree on the same visible evidence. The task rewards recovering distinct mechanisms within a fixed query budget. HypoSpace uses mechanical equivalence for transformations such as commutativity. For example, switching the inputs of AND does not add a discovery.

Walk through official validity, novelty, and recovery. Validity counts valid slots. Novelty counts unique in-space mechanical keys. Recovery compares valid discoveries with the benchmark's hidden reference keys. Emphasize the four-slot denominator for VR and NR, and the official reference-key denominator for RR.

Show why generation and evaluation were separated. D1 exposed a grammar mismatch. D2 tested repaired Prompts. In P3, all three Prompts passed aggregate validity but failed the Seed-robustness criterion. The highest observed P3 RR was P1's 10.67%. These numbers motivated the controller study and measure the historical Prompt conditions only. Hand over to Yujia Wang for the feedback available at inference time. [P3 measured results](p3_deep_analysis.md)

## Yujia Wang · Public validation and history (2:25)

Show the path from public observations to a single model response, the official Parser, the Grammar test, and the public Observation Validator. Explain that the hidden keys stay with an offline evaluator. No lookup of compatible answers is needed for the controller.

Use a small synthetic history with “NOT y”, “x OR y” and “y OR x”. The first candidate is outside basic Grammar. If the latter two fit the observations, the second valid form has the same mechanical key as the first. The structured history retains one example of that mechanism. It stores up to four recent first-valid discoveries, uses a fixed diagnostic for failures, and trims whole lines to its size limit.

Both online methods use the same history, so improvement from history alone is outside the primary contrast. Hand over to Jiayi Zhou for the difference between the policies. [History implementation](../scripts/candidate_history.py)

## Jiayi Zhou · Controllers and experimental controls (2:30)

ACB is a contextual bandit over three temperatures. At each step the context includes public observation size, remaining budget and recent validated feedback. We update only the arm that produced the response.

FSCB also tracks five public structural buckets. Explain that a leaf and a mixed-root structure contribute to different buckets, and that capacities are public Grammar properties. Once a valid key exists, a Frontier arm can add a short hint for the least covered available bucket. The same generated response can still earn credit for a different structure. The added first-discovery reward decays with the count already discovered in that bucket. Repeats and invalid responses share ACB's reward.

Describe the controls. Frozen Qwen2.5-3B, the same P1 base, same output limits, paired request Seeds and four requests per group per Seed. The four groups had no exposure in D1–P3 and are outside the reserved Pilot and formal splits. The 12 pairs arise from repeating four groups across three Seeds. Hand over to Wenkang Zhou for the evidence and limitations. [Protocol](exploratory_preregistration.md)

## Wenkang Zhou · Evidence and limitations (2:45)

**After a sealed official run:** show one chart of official RR, VR and NR by method and a second chart of paired RR differences. State the actual request count, failures, average delta, and variation by group. Explain how often Frontier became available and was actually used. Give the measured ablation result if it was separately authorized and completed. Otherwise say which mechanism the combined comparison leaves unresolved.

**If collection is still pending, use this narration for slide 8.** “I am Wenkang Zhou. The first row on this slide is the completed P3 Prompt comparison, which Qiulin discussed earlier. The 96 requests in the second row are a separate, planned comparison. We verified that both methods can consume the planned slots, that the runner archives the responses, and that the archived synthetic responses can be replayed by the official evaluator. Those tests measure correctness of our implementation and scoring path. They do not estimate a change in recovery rate. The third row is the registered reward ablation. It would add 48 requests with the coverage bonus set to zero. The same structural context and Frontier policy would remain available. That gives us a way to examine the reward mechanism separately, while keeping the separate timing visible as a limitation. We have no live efficacy estimate for either condition today.” [Historical P3 figure](figures/p3/p3-official-metrics.png)

Close with the key limitations. Only four distinct development groups, a small grammar, one local model and four requests per trajectory. Any observed difference is descriptive. Show the repository's frozen source, public instructions and access-controlled evidence inventory. Explain where to find official replay, the paired table and the benchmark documentation. [Repository reproduction conditions](../README.md#full-reproduction-and-private-evidence)
