# Repository Guidelines

## Project Structure

Read `PROJECT_BRIEF.md` before starting work. It defines the HypoSpace course deliverables and research scope. `README.md` documents setup, repository status and reproduction. `AGENTS.md` records the working rules for this repository.

- `scripts/` contains the ACB-Hypo and FSCB-Hypo controllers, shared candidate history, experiment runners and scoring tools.
- `tests/` contains the public synthetic checks and the full offline regression suite.
- `configs/` and `prompts/` contain experiment manifests, settings and frozen prompt templates.
- `docs/` contains the preregistration, historical analysis, implementation audit and speaking guide. The final PowerPoint is in `docs/presentation/`.
- `pyproject.toml` declares the project environment and `uv.lock` pins dependencies.

Inspect the workspace and Git configuration before creating or cloning files. Locate the pinned HypoSpace sibling checkout before using upstream code. Keep the ACB-Hypo and FSCB-Hypo implementation and runners separate from the upstream benchmark modules so the original Boolean Genetic Interaction benchmark remains runnable.

## Local Development

Use the Python 3.11 environment declared by the project and pinned dependencies. Check the local environment before running inference.

- `python --version` checks the active Python interpreter.
- `ollama list` lists installed models. `ollama ps` shows which models are loaded.
- `nvidia-smi` reports GPU availability and memory use.

Verify the model tag and smoke-test generation with `qwen2.5:3b` in Ollama. Check the upstream source for the actual model interface, parser, validator, canonicalizer, and evaluation entry points before calling them.

## Python Style

Use four spaces for indentation, `snake_case` for modules and functions, and `PascalCase` for classes. Keep changes small and follow upstream conventions when editing benchmark code.

## Testing and Experiments

Run the original Boolean benchmark as a baseline, then run a pilot on five observation sets with a small query budget before scaling. Check parsing, validation, deduplication, and logging. Use `test_<module>.py` for new Python tests and name cases after the behavior they verify.

Keep test-set answers out of controller inputs and rewards. Preserve the official HypoSpace evaluation definitions and keep the model, prompts, observation sets, budgets, and validation procedure consistent across comparisons. Record the model version, configuration, seeds, raw JSON outputs, actions, and rewards. Report measured results only. Keep API keys out of Git.

## Commits and Pull Requests

Use short, imperative commit subjects such as `Add Boolean benchmark smoke test`. In the PR, explain the change, link the relevant issue if one exists, and include verification commands and outcomes. For experiment changes, include the configuration and result paths.


# Project Instructions

- Read PROJECT_BRIEF.md before working.
- Verify repository APIs before using them.
- Preserve official benchmark functionality.
- Never fabricate experimental results.
- Keep experiments reproducible.
- Run small tests before full experiments.
- Prioritize the one-week delivery deadline.



## Research Integrity

- Preserve the proposed ACB-Hypo research direction.
- Do not replace LinUCB with another RL algorithm
  without explaining the motivation and obtaining approval.
- Do not claim novelty without checking related work.
- Do not introduce unnecessary algorithmic complexity.
- Clearly distinguish proposed methods from experimentally
  validated methods.


## GPU and Model Constraints

- Target GPU: NVIDIA RTX 4060, 8GB VRAM.
- Ollama is already installed.
- Prefer Qwen2.5-3B for initial local inference.
- Do not assume larger models fit into available VRAM.
- Check GPU utilization and memory before experiments.
- Do not download large models or install major dependencies
  without reporting the requirements first.
- Keep local inference as the preferred option.
- Use API inference as a fallback when justified.
- Never mix different models in the same baseline comparison.


## Development Workflow

- Work in small, independently testable stages.
- After each stage, report:
  1. Files created or modified.
  2. Implementation details.
  3. Tests executed.
  4. Actual test results.
  5. Remaining issues.
- Do not proceed to expensive experiments
  before the pilot experiment passes.
- Do not start a new major feature after the algorithm
  design is frozen.
- Preserve working checkpoints and experimental outputs.


## GitHub Repository

- Official project repository:
  https://github.com/wql4612/EEC4300GroupProject
- Remote name: origin
- Default branch: main.
- All final source code, experiment scripts, configurations,
  documentation, and presentation materials should be maintained
  in this repository where appropriate.
- Keep the working tree clean and commit changes regularly.
- Use meaningful commit messages.
- Do not force-push or overwrite remote history.
- Check git status and remote changes before committing.
- Keep API keys, credentials, private data, and large model
  files out of Git.
- Ensure the README contains setup, execution, and reproduction
  instructions before final submission.
