"""Export aggregate charts from a completed official exploratory or P3 run."""

import argparse
import hashlib
import json
from collections import defaultdict
from math import ceil, isclose
from pathlib import Path
from statistics import mean, stdev

METRICS = {"recovery_rate": "RR", "valid_rate": "VR", "novelty_rate": "NR"}
METHODS = ("ACB", "FSCB")


def audited_rates(trajectories, summary, methods, method_field):
    rates = {}
    for method in methods:
        rows = [row["official"] for row in trajectories if row[method_field] == method]
        if not rows:
            raise ValueError(f"Missing official trajectories for {method}")
        rates[method] = {}
        for metric in METRICS:
            values = [row[metric] for row in rows]
            if any(type(value) not in (int, float) or not 0 <= value <= 1 for value in values):
                raise ValueError(f"Invalid official {metric} for {method}")
            official_macro = mean(values)
            if not isclose(summary[method][metric], official_macro, rel_tol=0, abs_tol=1e-12):
                raise ValueError(f"Saved macro {metric} does not match the mean of official trajectories for {method}")
            rates[method][metric] = official_macro
    return rates


def paired_rates(trajectories):
    pairs = defaultdict(dict)
    groups = {group: f"G{i}" for i, group in enumerate(sorted({row["group_id"] for row in trajectories}), 1)}
    for row in trajectories:
        key = row["group_id"], row["seed"]
        if row["method"] not in METHODS or row["method"] in pairs[key]:
            raise ValueError("Unexpected or duplicate method in a paired trajectory")
        pairs[key][row["method"]] = {metric: row["official"][metric] for metric in METRICS}
    if not pairs or any(set(values) != set(METHODS) for values in pairs.values()):
        raise ValueError("Incomplete official trajectory pairs")
    return [
        {"group": groups[group], "seed": seed, "rates": results,
         "delta": {metric: results["FSCB"][metric] - results["ACB"][metric] for metric in METRICS}}
        for (group, seed), results in sorted(pairs.items())
    ]


def render_scorecard(summary, pairs, *, requests, failures):
    groups = len({pair["group"] for pair in pairs})
    lines = ["# ACB-Hypo versus FSCB-Hypo", "",
             f"{requests} archived requests, {failures} infrastructure failures, {len(pairs)} paired trajectories",
             f"across {groups} development groups. These results are descriptive.", "",
             "| Method | Official RR | Official VR | Official NR |", "| --- | ---: | ---: | ---: |"]
    for method, values in summary.items():
        lines.append(f"| {method}-Hypo | " + " | ".join(f"{values[metric]:.2%}" for metric in METRICS) + " |")
    lines += ["", "### FSCB minus ACB", "",
              "| Official metric | Mean (pp) | Sample SD (pp) | Positive / tie / negative |",
              "| --- | ---: | ---: | ---: |"]
    for metric, label in METRICS.items():
        delta = [100 * pair["delta"][metric] for pair in pairs]
        sd = f"{stdev(delta):.2f}" if len(delta) > 1 else "—"
        lines.append(f"| {label} | {mean(delta):+.2f} | {sd} | "
                     f"{sum(item > 0 for item in delta)} / {delta.count(0)} / {sum(item < 0 for item in delta)} |")
    lines += ["", "| Group | Seed | ACB RR | FSCB RR | Δ RR (pp) | Δ VR (pp) | Δ NR (pp) |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for pair in pairs:
        rr = "recovery_rate"
        values = " | ".join(f"{100 * pair['delta'][metric]:+.2f}" for metric in METRICS)
        lines.append(f"| {pair['group']} | {pair['seed']} | {pair['rates']['ACB'][rr]:.2%} | "
                     f"{pair['rates']['FSCB'][rr]:.2%} | {values} |")
    return "\n".join(lines) + "\n"


def verified_archive(run_dir, *, historical_p3):
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    seal = json.loads((run_dir / "collection_seal.json").read_bytes())
    journal = (run_dir / "requests.jsonl").read_bytes()
    count = 180 if historical_p3 else 96
    counts_ok = (
        seal["requests_bytes"] == len(journal) and seal["requests_record_count"] == count
        and seal["actual_requests"] == count and seal["planned_requests"] == count
        if historical_p3 else seal["bytes"] == len(journal) and seal["records"] == count
    )
    if (not counts_ok or len(journal.splitlines()) != count or not journal.endswith(b"\n")
            or seal["requests_sha256"] != digest(journal)
            or seal["freeze_sha256"] != digest((run_dir / "freeze.json").read_bytes())):
        raise ValueError("Archive does not match the sealed comparison")
    results = json.loads((run_dir / "official-results.json").read_bytes())
    if historical_p3:
        if results["collection_seal"] != seal or results["planned_requests"] != count:
            raise ValueError("P3 official scores do not match the seal")
    elif results["requests"] != count or results["infrastructure_failures"] != sum(
        json.loads(line)["infrastructure_error"] is not None for line in journal.splitlines()
    ):
        raise ValueError("Official request counts do not match the sealed journal")
    return results


def plot(path, summary, pairs=()):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    color = ("#09688c", "#cc5930", "#4d774e")
    if pairs:
        fig, (main, paired) = plt.subplots(1, 2, figsize=(12.8, 6.0), layout="constrained")
        fig.suptitle("Exploratory Boolean comparison", fontweight="bold")
        deltas = [p["delta"]["recovery_rate"] * 100 for p in pairs]
        size = max(5, max(map(abs, deltas)) * 1.13)
        paired.barh(range(len(pairs)), deltas, color=[color[0] if d >= 0 else color[1] for d in deltas])
        paired.axvline(0, color="#4d5358", linewidth=1)
        paired.scatter([0 for d in deltas if d == 0], [i for i, d in enumerate(deltas) if d == 0],
                       color="#4d5358", s=12, zorder=3)
        paired.set_yticks(range(len(pairs)), [f"{p['group']}  /  {p['seed']}" for p in pairs], fontsize=9)
        paired.set_xlim(-size, size)
        paired.invert_yaxis()
        paired.set_xlabel("FSCB − ACB recovery (percentage points)")
        paired.set_title("Paired official RR", loc="left")
        paired.spines[["top", "right"]].set_visible(False)
        paired.grid(axis="x", color="#e5e8e9")
        paired.set_axisbelow(True)
    else:
        fig, main = plt.subplots(figsize=(9.2, 5.2), layout="constrained")
        fig.suptitle("Historical P3 Prompt study", fontweight="bold")
        main.set_title("180 archived requests · 15 trajectories per Prompt", loc="left")
    width = .7 / len(summary)
    for i, (method, values) in enumerate(summary.items()):
        bars = main.bar([j + (i - (len(summary)-1)/2)*width for j in range(len(METRICS))],
                        [100 * values[metric] for metric in METRICS], width, label=method, color=color[i])
        main.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
    main.set_xticks(range(len(METRICS)), list(METRICS.values()))
    highest = 100 * max(value for rates in summary.values() for value in rates.values())
    upper = max(40, 20 * ceil((highest + 4) / 20))
    main.set_ylim(0, upper)
    main.set_yticks(range(0, min(upper, 100) + 1, 20))
    main.set_ylabel("Official trajectory macro average (%)")
    main.legend(loc="upper right", frameon=False)
    main.spines[["top", "right"]].set_visible(False)
    main.grid(axis="y", color="#e5e8e9")
    main.set_axisbelow(True)
    fig.savefig(path, dpi=165)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--historical-p3", action="store_true", help="Create a separately labeled historical P3 figure")
    args = parser.parse_args()
    results = verified_archive(args.run_dir, historical_p3=args.historical_p3)
    if args.historical_p3:
        rows = results["by_group_seed"]
        if len(rows) != 45 or {row["variant"] for row in rows} != {"P1", "P2", "P3"}:
            raise ValueError("Expected the 45 official P3 trajectories")
        summary = audited_rates(rows, results["summary"], ("P1", "P2", "P3"), "variant")
        args.output_dir.mkdir(parents=True, exist_ok=False)
        plot(args.output_dir / "p3-official-metrics.png", summary)
    else:
        rows = results["trajectories"]
        pairs = paired_rates(rows)
        if len(pairs) != 12 or len({pair["group"] for pair in pairs}) != 4:
            raise ValueError("Expected 12 paired trajectories across four groups")
        summary = audited_rates(rows, results["summary"], METHODS, "method")
        args.output_dir.mkdir(parents=True, exist_ok=False)
        plot(args.output_dir / "exploratory-official-metrics.png", summary, pairs)
        (args.output_dir / "exploratory-scorecard.md").write_text(
            render_scorecard(summary, pairs, requests=results["requests"], failures=results["infrastructure_failures"]),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
