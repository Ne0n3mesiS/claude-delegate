#!/usr/bin/env python3
"""
claude-delegate :: stats.py
Lit le journal des délégations et affiche l'économie cumulée réelle.
Usage : python3 stats.py
"""
import json
import os
from collections import defaultdict
from pathlib import Path

LOG_FILE = Path(os.path.expanduser("~/.claude/delegate/savings.jsonl"))


def main():
    if not LOG_FILE.exists():
        print("Aucune délégation enregistrée pour l'instant.")
        print("Lance une tâche avec : /delegate \"...\" ou `dg \"...\"`")
        return

    total_tok = 0
    total_usd = 0.0
    total_time = 0.0
    total_real_cost = 0.0
    n = 0
    by_model = defaultdict(lambda: {"n": 0, "tok": 0})
    by_cat = defaultdict(int)
    tier_count = defaultdict(int)

    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            total_tok += r.get("tokens_saved_est", 0)
            total_usd += r.get("usd_saved_est", 0.0)
            total_time += r.get("elapsed_s", 0.0)
            total_real_cost += r.get("cost_real", 0.0)
            tier_count[r.get("tier", "local")] += 1
            key = f"{r.get('backend','?')}/{r.get('model','?')}"
            by_model[key]["n"] += 1
            by_model[key]["tok"] += r.get("tokens_saved_est", 0)
            by_cat[r.get("category", "?")] += 1

    print("═" * 52)
    print("  claude-delegate — économie cumulée")
    print("═" * 52)
    print(f"  Tâches déléguées      : {n}")
    print(f"  Tokens Claude évités  : ~{total_tok:,}".replace(",", " "))
    print(f"  Valeur estimée        : ≈ ${total_usd:.2f}")
    print(f"  Coût réel engagé      : ${total_real_cost:.4f}  "
          f"(local & tiers gratuits = $0)")
    print(f"  Temps cumulé          : {total_time:.0f}s")
    print(f"  Répartition           : "
          f"{tier_count.get('local',0)} local · {tier_count.get('cloud',0)} cloud")
    print("─" * 52)
    print("  Par modèle / provider :")
    for k, v in sorted(by_model.items(), key=lambda x: -x[1]["tok"]):
        print(f"    {k:<32} {v['n']:>3} tâches  ~{v['tok']:,} tok".replace(",", " "))
    print("─" * 52)
    print("  Par catégorie :")
    for k, v in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"    {k:<12} {v} tâches")
    print("═" * 52)
    print("  Note : estimations indicatives (≈4 car/token).")


if __name__ == "__main__":
    main()
