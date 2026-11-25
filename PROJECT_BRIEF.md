# HypoSpace Final Project — Project Handoff Brief

## 1. Course Requirements

Course project: Part 1.2 Final Project (30%).

Requirements:

- Same team as Part 1.
- Develop a novel ML method to improve LLM performance on the HypoSpace benchmark.
- Submit a 10-minute group video presentation.
- Submit a public GitHub repository.
- Every team member must participate in the presentation.
- The whole team receives the same score.
- Grading criteria: Novelty, Importance, Quality.

Part 1 topic: Reinforcement Learning Fundamentals.

Benchmark paper:
https://proceedings.mlr.press/v306/chen26bz.html

## 2. Team Constraints

- Team size: 4 students.
- Remaining time: Less than 1 week.
- GPU: NVIDIA RTX 4060 with 8GB VRAM.
- Local inference runtime: Ollama, already installed.
- Available inference options: Local Ollama models and external LLM APIs.
- Experience: Basic RL knowledge, no practical LLM training experience.
- Priority: Complete implementation, reliable experiments, reproducibility.
- Avoid full LLM fine-tuning and unnecessarily complicated training frameworks.

## 3. Proposed Research Direction

Provisional project title:

ACB-Hypo: Adaptive Coverage Bandit for HypoSpace

Research theme:

An Online Reinforcement Learning Method for Improving LLM Hypothesis Coverage.

Main idea:

Keep the LLM parameters frozen and use a Contextual Multi-Armed Bandit to adaptively select inference-time sampling strategies based on online validation feedback.

The proposed method is an inference-time RL approach, not GRPO or full LLM fine-tuning.

Important: This is a proposed research direction, not an experimentally validated result. Check its novelty against existing literature and confirm whether inference-time RL satisfies the course requirement.

## 4. Proposed Algorithm

Primary algorithm: LinUCB Contextual Bandit.

Action space:

- Temperature 0.2
- Temperature 0.6
- Temperature 1.0

Context features:

- Bias term
- Observation complexity
- Remaining query budget
- Recent validity rate
- Recent novel valid hypothesis rate

Initial reward design:

- +1.0: Valid and previously undiscovered hypothesis.
- +0.2: Valid but duplicated hypothesis.
- -0.5: Invalid or unparsable hypothesis.

Use the official HypoSpace Validator and canonicalization mechanism whenever possible.

The controller should:

1. Observe current context.
2. Select a sampling temperature using LinUCB.
3. Generate one hypothesis.
4. Validate and canonicalize the result.
5. Calculate reward.
6. Update the selected arm.
7. Repeat until the generation budget is exhausted.

Do not use ground-truth answers from the test set as controller inputs or rewards.

## 5. Benchmark Scope

Primary task:
Boolean Genetic Interaction.

Optional secondary task:
Causal Graph Inference, only if the main experiments are completed early.

Do not initially attempt all three HypoSpace tasks.

First inspect the official repository and identify its actual:

- Data generation functions
- LLM interface
- Parser
- Validator
- Canonicalization
- Evaluation functions

Do not assume that previously suggested function names exist. The checked-out official source code is authoritative.

## 6. Experimental Design

Baselines:

1. Fixed Temperature 0.2
2. Fixed Temperature 0.6
3. Fixed Temperature 1.0
4. ACB-Hypo
5. Optional Random Temperature
6. Optional ACB ablation without novelty reward

Primary metrics:

- Validity Rate (VR)
- Novelty Rate (NR)
- Recovery Rate (RR)

Use the official benchmark definitions.

Experimental controls:

- Same LLM.
- Same observation sets.
- Same prompt.
- Same query budget.
- Same output length constraints.
- Same validation procedure.
- Multiple random seeds when feasible.

Initial pilot configuration:

- 5 observation sets.
- Small query budget.
- Verify end-to-end correctness before scaling.

Possible final configuration:

- 10–20 observation sets.
- 10–12 queries per set.
- At least 2–3 seeds, depending on runtime.

These are provisional settings. Adjust according to available compute and actual execution time.

Required outputs:

- Raw JSON results.
- Experiment configurations.
- Action selection logs.
- Reward logs.
- Mean and standard deviation.
- Performance comparison charts.
- At least one ablation study.

Never fabricate experimental results.

## 7. Technical Environment and Model Strategy

### Preferred Environment

- Python 3.10 or 3.11
- NVIDIA RTX 4060, 8GB VRAM
- Ollama (already installed)
- NumPy
- Matplotlib
- Existing HypoSpace benchmark utilities
- Python requests or a compatible API client

### Local Model Strategy

Preferred initial model:

- Qwen2.5-3B-Instruct through Ollama.

Example:
`ollama pull qwen2.5:3b`

The exact model tag and quantization should be verified locally.

Do not assume the model fully fits into GPU memory without checking actual runtime allocation.

Check:

- `nvidia-smi`
- `ollama ps`
- Model loading behavior
- Generation latency
- Output format consistency
- GPU memory usage

Do not use a larger model as the default before completing a successful 3B pilot.

### API Fallback Strategy

If local inference is too slow or model output quality is insufficient, use one consistent API model for the entire experimental comparison.

Do not mix different models within the same baseline comparison.

Record:

- Provider
- Exact model identifier
- Model version or snapshot if available
- API configuration
- Prompt
- Temperature
- Top-p
- Maximum output tokens
- Random seed, if supported

Never commit API keys or credentials to GitHub.

### Experimental Fairness

For every experimental comparison:

- Use the same underlying LLM.
- Keep prompts identical.
- Keep output budgets identical.
- Keep generation parameters identical except for the variable under investigation.
- Use the same Validator and Canonicalizer.
- Record all model configurations.

Local and API experiments must be reported separately if different models are used.

### Implementation Priority

1. Verify existing Ollama installation.
2. Run a small local model generation test.
3. Verify dynamic Temperature support through Ollama.
4. Implement the HypoSpace-compatible LLM interface.
5. Run the original Boolean Benchmark.
6. Implement ACB-Hypo.
7. Only then consider API fallback or additional models.

Do not spend time reinstalling an already working runtime.

## 8. Team Responsibilities

Member A — Benchmark and Dataset:

- Paper analysis.
- Dataset generation.
- Official benchmark reproduction.
- Baseline verification.

Member B — LLM and Engineering:

- GPU inspection.
- Model runtime.
- LLM interface.
- Output parsing and debugging.

Member C — RL Algorithm:

- LinUCB.
- Context extraction.
- Reward design.
- ACB-Hypo integration.

Member D — Experiments and Presentation:

- Experimental matrix.
- Evaluation.
- Statistical analysis.
- Figures.
- README and presentation coordination.

All four members must understand the overall method.

## 9. Seven-Day Execution Plan

Note: This is a reference schedule.
Codex must compress the plan according to the actual
remaining deadline and prioritize a working end-to-end pipeline.

Day 1: Environment setup and official benchmark reproduction.

Day 2: Fixed-temperature baselines.

Day 3: Implement LinUCB and ACB-Hypo.

Day 4: Pilot experiments, debugging and algorithm freeze.

Day 5: Formal experiments and ablation.

Day 6: Result analysis, GitHub documentation and slides.

Day 7: Final verification and 10-minute video recording.

Prioritize a working end-to-end pipeline over additional features.

## 10. Required Codex Working Principles

- Inspect the repository before modifying files.
- Verify all existing APIs and functions from source code.
- Do not invent function names, benchmark results or experimental claims.
- Preserve official benchmark functionality.
- Implement in small, testable stages.
- Run a small pilot before expensive experiments.
- Keep experiment configurations and random seeds.
- Log all generated outputs and rewards.
- Avoid test-set leakage.
- Keep secrets and API keys out of Git.
- Explain every important implementation decision.
- Do not start large-scale experiments before the pilot passes.
- Prefer reproducibility and completeness over unnecessary complexity.

## 11. Immediate Next Step

Start with repository inspection and project planning only.

Do not immediately implement the complete algorithm.

First:

1. Inspect the current workspace.
2. Locate or clone the official HypoSpace repository after confirming its source.
3. Read the relevant Boolean benchmark implementation.
4. Verify the actual model interface, parser, validator and evaluation APIs.
5. Check available GPU memory.
6. Identify any incompatibilities with the proposed ACB-Hypo design.
7. Check the feasibility and research novelty of the proposed method.
8. Produce a concrete implementation plan with file changes, tests and estimated execution time.

Wait for user approval before beginning substantial implementation.

## Project Repository

GitHub Repository:
https://github.com/wql4612/EEC4300GroupProject

Repository Purpose:
This is the official public repository for the EEC4300
Part 1.2 Final Group Project.

All project deliverables should be organized here, including:

- ACB-Hypo algorithm implementation
- Benchmark integration
- Experiment scripts and configurations
- Experimental results and analysis
- Reproducibility documentation
- Final README
- Presentation materials (where appropriate)

The repository must remain public for course submission.

Important:
This repository is our team's implementation repository.
It is NOT the official upstream HypoSpace benchmark repository.

Keep our implementation separate from the original benchmark
source code and preserve the original benchmark functionality.
