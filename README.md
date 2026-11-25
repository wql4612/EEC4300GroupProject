# ACB-Hypo

EEC4300 Part 1.2 Final Group Project. The first checkpoint reproduces a small official HypoSpace Boolean run with a fixed-temperature local model.

## Set up D1

Run from the team repository root in PowerShell. The upstream repository is a sibling of the team repository.

```powershell
uv python install 3.11.15 --no-bin --no-registry
uv venv --managed-python --python 3.11.15 .venv
uv sync --locked --python .venv/Scripts/python.exe
uv pip check --python .venv/Scripts/python.exe

git clone --no-checkout https://github.com/CTT-Pavilion/_HypoSpace.git ../HypoSpace
git -C ../HypoSpace checkout --detach c69e9318577b34b5b896996571aefd4ba6053f58
git -C ../HypoSpace status --short --branch
ollama list
```

Use a running local Ollama server with `qwen2.5:3b` already installed. The six direct Python dependencies are the imports required by upstream's Boolean generator, benchmark, and LLM interface. `uv.lock` pins them and their transitive dependencies.

## Reproduce the Boolean smoke test

```powershell
$env:PYTHONHASHSEED = '0'
$env:PYTHONUTF8 = '1'
$run = "results/d1/smoke-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Force -Path 'data/d1', $run | Out-Null

.\.venv\Scripts\python.exe -B ../HypoSpace/boolean/boolean_dataset.py --operators basic --max-depth 2 --seed 33550336 --output data/d1/boolean_basic_depth2.json 2>&1 |
    Tee-Object -FilePath "$run/dataset.log"
if ($LASTEXITCODE -ne 0) { throw 'Upstream dataset generation failed.' }

uv pip freeze --python .venv/Scripts/python.exe | Set-Content -Encoding utf8 "$run/dependencies.txt"
.\.venv\Scripts\python.exe -B scripts/run_boolean_smoke.py --output $run 2>&1 |
    Tee-Object -FilePath "$run/benchmark.log"
if ($LASTEXITCODE -ne 0) { throw 'Official Boolean smoke failed. See the saved logs.' }
```

The script uses upstream's `BooleanBenchmarkRefined.run_benchmark()` and `LLMInterface` with the versioned settings in `configs/boolean_smoke.json`. The sample has three fixed-budget slots. The upstream evaluator may retry a slot, and `requests.jsonl` records every HTTP request, including errors and responses rejected by the Boolean syntax guard. The upstream evaluator decides which Boolean operators are in space. Each request uses the configured base model seed plus its zero-based request index. The smoke check requires a scoreable official result with at least one recovered hypothesis per sample.

At the pinned upstream revision, the basic operator set contains AND and OR. The generated dataset is under `data/d1/`. Each run directory has the official `result.json` and checkpoint, the request journal, dataset and benchmark console logs, and `metadata.json` with the data hash, dependency versions, model digest, upstream commit and runtime configuration. The generated dataset contains answer keys and is reserved for benchmark evaluation. Both generated directories are Git-ignored.

The upstream CLI stays available independently. Run `.\.venv\Scripts\python.exe -B ../HypoSpace/boolean/boolean_benchmark.py --help` to inspect its options. The D1 output checks connectivity to the benchmark and supplies a reproducible development fixture.

On the initial D1 sample, Qwen generated three parseable responses that used an operator outside the basic dataset space. The benchmark emitted VR, NR, and RR of zero, and the smoke command exited with status 1. An explicitly labeled offline positive control confirmed that upstream scored two equivalent, manually supplied valid expressions as VR = 1, NR = 0.5, and RR = 0.5. The positive control is separate from the model result.
