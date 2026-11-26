# EEC4300 final presentation

Ten minutes, four speakers. The historical P3 Prompt comparison is complete and officially scored. The 96-slot ACB–FSCB comparison and the 48-slot reward ablation are frozen and have not been run. Synthetic tests verify the implementation and offline scoring pipeline. FSCB's live efficacy is unmeasured. [Final editable PowerPoint](presentation/EEC4300-final.pptx)

| Time | Speaker | Screen |
| --- | --- | --- |
| 00:00–02:20 | Qiulin Wu | Task, benchmark metrics, D1–P3 evidence |
| 02:20–04:45 | Yujia Wang | Candidate history and online validation |
| 04:45–07:15 | Jiayi Zhou | ACB, FSCB, and the experimental controls |
| 07:15–10:00 | Wenkang Zhou | Experiment status, limitations and reproduction |

## Qiulin Wu · Boolean mechanism recovery (2:20)

Show a synthetic Boolean observation, such as `(x=0, y=0) → 0`. Explain how several formulas can agree on the same visible evidence. The task rewards recovering distinct mechanisms within a fixed query budget. HypoSpace uses mechanical equivalence for transformations such as commutativity. For example, switching the inputs of AND does not add a discovery.

Walk through official validity, novelty, and recovery. Validity counts valid slots. Novelty counts unique in-space mechanical keys. Recovery compares valid discoveries with the benchmark's hidden reference keys. Emphasize the four-slot denominator for VR and NR, and the official reference-key denominator for RR.

Show why generation and evaluation were separated. D1 exposed a grammar mismatch. D2 tested repaired Prompts. In P3, all three Prompts passed aggregate validity but failed the Seed-robustness criterion. The highest observed P3 RR was P1's 10.67%. These numbers motivated the controller study and measure the historical Prompt conditions only. Hand over to Yujia Wang for the feedback available at inference time. [P3 measured results](p3_deep_analysis.md)

## Yujia Wang · Public validation and history (2:25)

Show the path from public observations to a single model response, the official Parser, the Grammar test, and the public Observation Validator. Explain that the hidden keys stay with an offline evaluator. No lookup of compatible answers is needed for the controller.

Use a small synthetic history with “NOT y”, “x OR y” and “y OR x”. The first candidate is outside basic Grammar. If the latter two fit the observations, the second valid form has the same mechanical key as the first. The structured history retains one example of that mechanism. It stores up to four recent first-valid discoveries, uses a fixed diagnostic for failures, and trims whole lines to its size limit.

Both online methods use the same history, so improvement from history alone is outside the primary contrast. Hand over to Jiayi Zhou for the difference between the policies. [History implementation](../scripts/candidate_history.py)

## Jiayi Zhou · Controllers and experimental controls (2:30)

ACB is a contextual bandit over three temperatures. Slide 6 shows each method's context.

- ACB `x_t = [1, n_obs/4, (B-t)/B, recent_valid_rate, recent_unique_valid_rate]`
- FSCB `x_t = [1, n_obs/4, (B-t)/B, recent_valid_rate, recent_unique_valid_rate, occupied_bins/5]`

Both share the first five inputs. Only FSCB adds the occupied-bucket fraction. The inputs use a fixed bias, public observations, remaining budget and online validation history. Ground Truth stays offline. LinUCB updates only the selected arm.

FSCB also tracks five public structural buckets. Explain that a leaf and a mixed-root structure contribute to different buckets, and that capacities are public Grammar properties. Once a valid key exists, a Frontier arm can add a short hint for the least covered available bucket. The same generated response can still earn credit for a different structure. The added first-discovery reward decays with the count already discovered in that bucket. Repeats and invalid responses share ACB's reward.

Describe the controls. Frozen Qwen2.5-3B, the same P1 base, same output limits, paired request Seeds and four requests per group per Seed. The four groups had no exposure in D1–P3 and are outside the reserved Pilot and formal splits. The 12 pairs arise from repeating four groups across three Seeds. Hand over to Wenkang Zhou for the evidence and limitations. [Protocol](exploratory_preregistration.md)

## Wenkang Zhou · Evidence and limitations (2:45)

### Slide 8 · Experiment status (07:15–08:40)

> At the time of this presentation, the historical P3 Prompt comparison is complete and officially scored. The 96-slot ACB–FSCB comparison and the separate 48-slot reward ablation have been frozen but have not been run. Our synthetic tests verify the implementation and offline scoring pipeline, but they do not measure live model efficacy. Therefore, we make no claim that FSCB outperforms ACB.

### Slide 9 · Scope and reproducibility (08:40–10:00)

The exploratory design has four distinct development groups, a small Grammar, one local model and four requests per trajectory. Any eventual paired difference would be descriptive. The public repository contains the frozen source, configuration and synthetic tests. The historical P3 archive contains the responses and reference keys used for its official scoring. Raw responses will be sealed for official offline replay after collection in the exploratory comparisons. [Repository reproduction conditions](../README.md#full-reproduction-and-private-evidence)
