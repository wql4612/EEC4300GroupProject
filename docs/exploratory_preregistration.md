# ACB-Hypo vs. FSCB-Hypo exploratory comparison

**Registered 2026-11-27. Awaiting live-run authorization.** This study uses the four unused development groups in the existing Boolean basic allocation. It estimates performance on those groups under a short budget. The five-group P3 gate, Pilot allocation and completed D1–P3 archives retain their original definitions. This is a separate exploratory study. [Independent data audit](independent_dataset_feasibility.md) · [P3 outcome](p3_deep_analysis.md)

## Sample and budget

| Group | Public observation SHA-256 |
| --- | --- |
| `n2_inputs_00_01_outputs_01` | `963dce743fe39e362198ed39f899563259d779efd5090727cdbfc83aa464c47e` |
| `n2_inputs_01_10_outputs_11` | `7bbdb395e1f77786847fdaed14017e39d310b5b6c46d1c57c22236449198a210` |
| `n3_inputs_00_01_10_outputs_010` | `a562830e248a591c07f53cc445e20d1bc95d98a06d969ee6189d48862ae43187` |
| `n3_inputs_01_10_11_outputs_011` | `d135091c940c629916ddabb0868a8b377392d900b5c055281bb741120f99e550` |

Use all four groups. The offline preparer verifies IDs and observation fingerprints against D1, D2 and P3 exposure and against the Pilot and formal allocations. Group selection uses allocation and public observations. No answers or recovery outcomes enter selection.

Four groups × three rollout Seeds (42, 271828, 314159) × two methods × four slots gives **96 requests**, 48 per method, with 12 paired Group–Seed trajectories. Group order is the sorted ID list shuffled by each rollout Seed using the frozen Python version. Both methods receive identical model Seeds at paired slots, assigned as rollout Seed + four times the group-order index + the zero-based slot. Request order alternates between methods each slot, with the first method balanced across groups. The manifest fixes every order. One send per slot. Infrastructure failure stays in the denominator, skips controller updates and is never retried. An interrupted study stays incomplete and its partial journal is retained. [Public copy of the frozen online manifest](../configs/exploratory_online_manifest.json)

## Methods

Both methods use the frozen `qwen2.5:3b` model and Template under Ollama 0.40.14, `num_ctx=4096`, `num_predict=128`, and the frozen P1 base Prompt. Both use the same Structured Candidate History. Its display retains at most four most recent first valid unique keys, with the latest fixed failure diagnostic when it fits. The history block has a 128-byte UTF-8 ceiling. An optional Frontier line has a separate 128-byte ceiling. Bytes serve as conservative local length controls. Report actual prompt and completion Token counts recorded by Ollama, where available. [Frozen P1](../prompts/d2/P1.txt) · [History policy](../scripts/candidate_history.py)

| Setting | ACB-Hypo | FSCB-Hypo |
| --- | --- | --- |
| Arms | Standard × temperature {0.2, 0.6, 1.0} | Standard or Frontier × the same temperatures |
| Context | Bias, observed-row fraction, remaining budget fraction, recent valid and first-valid-new rates | Same five inputs plus occupied bucket fraction |
| Reward | First valid new key +1, valid duplicate +0.2, invalid −0.5 | First valid new key `0.75 + 0.25 × exp(−c)`, with `c` the prior count in its structural bucket. Other rewards match ACB |
| Frontier | Standard base Prompt throughout | Among eligible buckets, target lowest count/capacity once any new key exists. Seeded ties, two targeted misses pause that bucket for the group |

Use disjoint LinUCB with ridge 1, exploration 0.5, seeded ties, and two feedback events per available arm as warm-up before UCB selection. A method–Seed controller learns across groups in the frozen order. Each new group clears its own history, coverage and recent-feedback window. All structure and reward signals come from the official Parser, public Grammar, official Validator and mechanical-key deduplication. The online collector reads a validated public manifest and denies answer-file access before the first request. The baseline keeps the same history so the comparison targets FSCB's controller and structural feedback package. [Implementation](../scripts/fscb_hypo.py)

## Scoring and reporting

Replay sealed responses in a separate offline process with the pinned HypoSpace Evaluator and one attempt per slot. Compare replayed online decisions to the archived prompts, options and rewards. Official RR is the primary descriptive outcome. Report Official VR and NR alongside it, taking the equal-weight mean across the 12 complete trajectories per condition. Report all 12 raw paired FSCB-minus-ACB differences, and their mean, sample SD, wins/ties/losses. Also report arm availability, exposures, successful updates, target hits, first valid discoveries, infrastructure failures, and token and time costs.

There are four observation groups repeated across three Seeds. Treat trajectory pairs as descriptive units and show group-level variation. The sample does not support a significance claim or independent generalization beyond these groups. A two-method comparison estimates the combined FSCB mechanism. It cannot attribute an effect to either the Frontier or the marginal reward alone.

**Separate reward ablation.** The 48-slot follow-up sets FSCB's `beta=0`, keeping the same group–Seed–slot mapping, Frontier, context and online-history policy. Its freeze binds the primary freeze and the exact FSCB slot projection. Each no-bonus policy starts with fresh state per Seed. Pair its official scores with the original sealed Full FSCB trajectories and report Full minus no-bonus. The ablation starts after primary scoring and requires separate authorization. Report the separate execution period as a limitation even when the runtime and model digests match. The primary 96-slot study does not include this arm. [Public copy of the frozen ablation manifest](../configs/exploratory_ablation_manifest.json)

## Reproduce and review

The commands assume the pinned sibling HypoSpace checkout, locked Python environment, and access to the private D1/D2/P3 allocation evidence and answer-bearing dataset. Keep the public online manifest and answer archive separate. `prepare` creates a new run directory and refuses to overwrite an existing one. `preflight` uses local version, listener, model-file and GPU checks without generating.

```powershell
$env:PYTHONHASHSEED = '0'
$env:PYTHONUTF8 = '1'
$run = 'results/exploratory/final-24h-20261127'
.\.venv\Scripts\python.exe -B tests/run_offline.py
.\.venv\Scripts\python.exe -B -m scripts.run_exploratory prepare $run
.\.venv\Scripts\python.exe -B -m scripts.run_exploratory preflight $run
```

For an already prepared bundle, start with `preflight`. After explicit live-run authorization, the entry point is `scripts.run_exploratory collect <run_dir> --authorized`. After collection, `scripts.run_exploratory score <run_dir>` runs offline and refuses to overwrite existing scores. A scorer that flags an expression for interpreter safety requires an exact-string review before full official replay. It preserves the original response. Raw HTTP bodies, full official scores and manifests remain in the private run directory. [Repository setup and private evidence](../README.md#full-reproduction-and-private-evidence)

After scoring, use a Python environment with Matplotlib 3.10.6 to export a reviewable scorecard and plot. This separate reporting step validates the request seal and checks the saved macro scores against the official trajectory rates. It exports only metrics, group aliases and Seed values.

```powershell
python -B -m scripts.report_exploratory $run results/exploratory/reviewable-summary
```

Prepare the separate reward ablation without generation using the same frozen primary directory.

```powershell
$ablation = 'results/exploratory/final-24h-no-bonus-20261127'
.\.venv\Scripts\python.exe -B -m scripts.run_exploratory_ablation prepare $run $ablation
```

For a prepared bundle, skip the prepare step. Once the primary run is scored and the follow-up is explicitly authorized, use `scripts.run_exploratory_ablation collect <primary_dir> <ablation_dir> --authorized`. Then run `scripts.run_exploratory_ablation score <primary_dir> <ablation_dir>` offline. The scorer replays both sealed journals, checks the primary official output, and pairs the two FSCB conditions. [Ablation runner](../scripts/run_exploratory_ablation.py)
