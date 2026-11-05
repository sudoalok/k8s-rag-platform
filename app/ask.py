"""Station 5 CLI: ask one question from the terminal."""

import sys

from rag import answer

if __name__ == "__main__":
    r = answer(" ".join(sys.argv[1:]))
    print(f"\nQ: {r['question']}\n")
    for s in r["sources"]:
        print(f"  {s['score']:.3f}  {s['file']}")
    if not r["grounded"]:
        print(f"\n(best score {r['best_score']} is below the cutoff -- Qwen not asked)")
    print(f"\nA: {r['answer']}\n")
