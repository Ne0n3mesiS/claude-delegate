#!/usr/bin/env python3
"""
claude-delegate :: delegate.py
Point d'entrée principal de la délégation.

Usage :
    python3 delegate.py "ta tâche ici"
    python3 delegate.py --category code "écris un quicksort"
    python3 delegate.py --json "résume ce texte"     # sortie machine
    python3 delegate.py --dry-run "..."              # montre le routing sans générer

Fait :
  1. découvre les backends locaux
  2. choisit le meilleur modèle pour la tâche
  3. génère la réponse localement (0 token Claude)
  4. logue l'économie estimée dans ~/.claude/delegate/savings.jsonl
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Permet d'importer router.py qu'on soit appelé depuis n'importe où
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router  # noqa: E402
import providers as cloud  # noqa: E402

LOG_DIR = Path(os.path.expanduser("~/.claude/delegate"))
LOG_FILE = LOG_DIR / "savings.jsonl"

# Coût de référence pour estimer la valeur de l'économie (Opus, $/Mtok output).
# Modifiable via env. Sert UNIQUEMENT à afficher une estimation indicative.
PRICE_PER_MTOK = float(os.environ.get("DELEGATE_PRICE_PER_MTOK", "75"))


def log_saving(record):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _try_local(task, available, category, temp, system, exclude_model=None):
    """Tente une génération locale. Retourne (backend, model, output) ou lève."""
    # On retire le modèle déjà échoué pour le fallback local->local
    pool = available
    if exclude_model:
        pool = []
        for b in available:
            models = [m for m in b["models"] if m != exclude_model]
            if models:
                pool.append({**b, "models": models})
    if not pool:
        raise RuntimeError("no_local_alternative")
    name, model, cat, base = router.pick_model(task, pool, category)
    output = router.generate(base, model, task, system=system, temperature=temp)
    return name, model, output


def run(task, category=None, dry_run=False, system=None, temperature=None):
    t0 = time.time()
    available = router.discover()
    cloud_chain = cloud.load_providers()

    # Aucun local ET aucun cloud configuré -> rien à faire
    if not available and not cloud_chain:
        return {
            "ok": False,
            "error": "no_backend",
            "message": ("Aucun modèle local détecté et aucun provider cloud "
                        "configuré. Démarre Ollama (`ollama serve`) ou configure "
                        "~/.claude/delegate/providers.json."),
        }

    # Catégorie / température (utilise le local si dispo pour deviner, sinon défaut)
    if available:
        _, _, cat, _ = router.pick_model(task, available, category)
    else:
        cat = category or router.DEFAULT_CATEGORY
    temp = temperature if temperature is not None else router.category_temperature(cat)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "backend": (available[0]["name"] if available else None),
            "category": cat,
            "temperature": temp,
            "available_backends": [b["name"] for b in available],
            "cloud_cascade": [p["name"] for p in cloud_chain],
        }

    events = []          # journal des bascules pour transparence
    route = []           # chaîne réellement parcourue

    # --- 1) Tentative locale principale -------------------------------------
    failed_model = None
    if available:
        try:
            name, model, output = _try_local(task, available, category, temp, system)
            return _finish_local(task, name, model, cat, output, t0, route + [name], events)
        except Exception as e:
            failed_model = locals().get("model")
            events.append("local principal indisponible (%s)" % str(e)[:80])
            route.append("local✗")

        # --- 2) Fallback local -> local (autre modèle installé) -------------
        try:
            name, model, output = _try_local(task, available, category, temp, system,
                                              exclude_model=failed_model)
            events.append("repli sur autre modèle local")
            return _finish_local(task, name, model, cat, output, t0,
                                 route + [name], events)
        except Exception as e:
            events.append("pas d'alternative locale (%s)" % str(e)[:60])
            route.append("local-alt✗")

    # --- 3) Cascade cloud (uniquement si configurée) ------------------------
    if cloud_chain:
        res = cloud.cascade(task, system=system, temperature=temp,
                            on_event=events.append)
        if res:
            return _finish_cloud(task, res, cat, t0, route + [res["provider"]], events)

    return {
        "ok": False,
        "error": "all_failed",
        "message": "Tous les backends (local + cloud) ont échoué.",
        "events": events,
    }


def _finish_local(task, name, model, cat, output, t0, route, events):
    elapsed = round(time.time() - t0, 1)
    out_tok = router.estimate_tokens(output)
    in_tok = router.estimate_tokens(task)
    saved = out_tok + in_tok
    usd_saved = round(saved / 1_000_000 * PRICE_PER_MTOK, 4)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tier": "local",
        "backend": name,
        "model": model,
        "category": cat,
        "tokens_saved_est": saved,
        "usd_saved_est": usd_saved,
        "cost_real": 0.0,
        "elapsed_s": elapsed,
        "route": route,
        "task_preview": task[:80],
    }
    log_saving(record)
    return {"ok": True, "tier": "local", "backend": name, "model": model,
            "category": cat, "elapsed_s": elapsed, "tokens_saved_est": saved,
            "usd_saved_est": usd_saved, "cost_real": 0.0, "route": route,
            "events": events, "output": output}


def _finish_cloud(task, res, cat, t0, route, events):
    elapsed = round(time.time() - t0, 1)
    saved = res["usage"]["in"] + res["usage"]["out"]
    usd_saved = round(saved / 1_000_000 * PRICE_PER_MTOK, 4)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tier": "cloud",
        "backend": res["provider"],
        "model": "(cloud)",
        "category": cat,
        "tokens_saved_est": saved,
        "usd_saved_est": usd_saved,
        "cost_real": res["cost"],
        "elapsed_s": elapsed,
        "route": route,
        "task_preview": task[:80],
    }
    log_saving(record)
    return {"ok": True, "tier": "cloud", "backend": res["provider"],
            "category": cat, "elapsed_s": elapsed, "tokens_saved_est": saved,
            "usd_saved_est": usd_saved, "cost_real": res["cost"], "route": route,
            "events": events, "output": res["text"]}


def main():
    ap = argparse.ArgumentParser(description="Délègue une tâche à un modèle local.")
    ap.add_argument("task", nargs="+", help="La tâche à déléguer")
    ap.add_argument("--category", choices=list(router.TASK_PROFILES.keys()),
                    help="Force la catégorie (sinon auto-détectée)")
    ap.add_argument("--system", help="Message système optionnel")
    ap.add_argument("--temperature", type=float,
                    help="Force la température (sinon : 0.2 pour etl, 0.7 sinon)")
    ap.add_argument("--json", action="store_true", help="Sortie JSON brute")
    ap.add_argument("--dry-run", action="store_true", help="Montre le routing sans générer")
    args = ap.parse_args()

    task = " ".join(args.task)
    res = run(task, category=args.category, dry_run=args.dry_run,
              system=args.system, temperature=args.temperature)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if not res["ok"]:
        print(f"❌ {res.get('message', res.get('error'))}", file=sys.stderr)
        sys.exit(1)

    if res.get("dry_run"):
        if res.get("backend"):
            print(f"→ routerait vers : {res['backend']} "
                  f"(catégorie : {res['category']}, température : {res['temperature']})")
            print(f"  backends locaux actifs : {', '.join(res['available_backends'])}")
        else:
            print(f"→ aucun local — passerait par le cloud "
                  f"(catégorie : {res['category']})")
        if res.get("cloud_cascade"):
            print(f"  cascade cloud : {' → '.join(res['cloud_cascade'])}")
        else:
            print("  cascade cloud : (aucune — mode 100% local)")
        return

    print(res["output"])
    print("", file=sys.stderr)

    # affiche les bascules éventuelles (transparence)
    for ev in res.get("events", []):
        print(f"   ⤷ {ev}", file=sys.stderr)

    if res["tier"] == "local":
        target = f"{res['backend']}/{res['model']}"
        cost_note = "0 token Claude · $0 local"
    else:
        target = f"cloud:{res['backend']}"
        cost_note = f"coût réel ≈ ${res['cost_real']}"

    print(f"─── délégué à {target} ({res['category']}) en {res['elapsed_s']}s · "
          f"~{res['tokens_saved_est']} tokens Claude économisés "
          f"(≈ ${res['usd_saved_est']}) · {cost_note} ───", file=sys.stderr)


if __name__ == "__main__":
    main()
