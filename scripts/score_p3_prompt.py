"""Score a sealed P3 validation run with the official Boolean evaluator."""

from scripts.p3_preflight import SELECTION_FILE
from scripts.run_d2_prompt import P3_VARIANTS
from scripts.score_d2_prompt import main as score_prompt


def main():
    score_prompt(variants=P3_VARIANTS, selection_file=SELECTION_FILE)


if __name__ == "__main__":
    main()
