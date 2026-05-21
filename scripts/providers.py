#!/usr/bin/env python3
"""
claude-delegate :: providers.py
Cascade de repli cloud OPTIONNELLE et générique.

Philosophie : le plugin reste 100% local et gratuit par défaut. Si — et
seulement si — l'utilisateur configure des providers cloud (typiquement des
tiers GRATUITS comme Gemini Flash, Groq, OpenRouter free), le plugin peut
basculer dessus quand le local échoue ou sature.

Aucune clé n'est embarquée dans le plugin : l'utilisateur déclare les siennes.
Tout provider exposant une API compatible OpenAI (/chat/completions) marche,
sans modifier le code — d'où le format générique.

Ordre de résolution de la config :
  1. variable d'env DELEGATE_PROVIDERS (chemin vers un JSON)
  2. ~/.claude/delegate/providers.json
  3. rien -> cascade cloud vide (100% local)

Les clés peuvent être inline OU référencer une variable d'env via "env:NOM".
Détection de quota épuisé : HTTP 429 (rate/quota) et 503 (saturation) -> on
passe au provider suivant. Toute autre erreur -> on passe aussi au suivant,
en la journalisant.

Stdlib uniquement.
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_ENV = os.environ.get("DELEGATE_PROVIDERS")
DEFAULT_CONFIG = Path(os.path.expanduser("~/.claude/delegate/providers.json"))

CLOUD_TIMEOUT = 120  # un appel cloud ne devrait pas traîner autant qu'un local


def _resolve_key(value):
    """Permet "env:GEMINI_API_KEY" -> lit la variable d'env. Sinon valeur brute."""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value or ""


def load_providers():
    """
    Charge la cascade de providers cloud. Retourne une liste ordonnée :
    [{name, base_url, api_key, model, price_per_mtok_in, price_per_mtok_out}, ...]
    Liste vide si aucune config -> comportement 100% local conservé.
    """
    path = Path(CONFIG_ENV) if CONFIG_ENV else DEFAULT_CONFIG
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []

    providers = raw.get("providers", raw) if isinstance(raw, dict) else raw
    out = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        key = _resolve_key(p.get("api_key", ""))
        # un provider sans clé résolue est ignoré (silencieusement : la clé peut
        # juste ne pas être définie dans cet environnement)
        if not key and not p.get("no_key_required"):
            continue
        out.append({
            "name": p.get("name", "cloud"),
            "base_url": p.get("base_url", "").rstrip("/"),
            "api_key": key,
            "model": p.get("model", ""),
            "price_in": float(p.get("price_per_mtok_in", 0) or 0),
            "price_out": float(p.get("price_per_mtok_out", 0) or 0),
        })
    return out


def call_provider(provider, task_text, system=None, temperature=None):
    """
    Appelle un provider cloud (OpenAI-compatible). Retourne un dict :
      {ok: True, text, usage:{in,out}, cost}   en cas de succès
      {ok: False, status, reason}              en cas d'échec
    status 429/503 = saturation/quota -> l'appelant passe au suivant.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task_text})
    payload = {"model": provider["model"], "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature

    req = urllib.request.Request(provider["base_url"] + "/chat/completions")
    req.add_header("Content-Type", "application/json")
    if provider["api_key"]:
        req.add_header("Authorization", "Bearer " + provider["api_key"])
    req.data = json.dumps(payload).encode("utf-8")

    try:
        with urllib.request.urlopen(req, timeout=CLOUD_TIMEOUT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 429 = quota/rate limit épuisé ; 503 = service saturé -> suivant
        return {"ok": False, "status": e.code,
                "reason": "quota/rate" if e.code == 429 else
                          ("saturé" if e.code == 503 else "http %d" % e.code)}
    except Exception as e:
        return {"ok": False, "status": 0, "reason": str(e)[:120]}

    try:
        text = d["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return {"ok": False, "status": 0, "reason": "réponse inattendue"}

    # Usage réel si fourni par le provider, sinon estimation
    usage = d.get("usage", {})
    tin = usage.get("prompt_tokens", max(1, len(task_text) // 4))
    tout = usage.get("completion_tokens", max(1, len(text) // 4))
    cost = round(tin / 1e6 * provider["price_in"] + tout / 1e6 * provider["price_out"], 6)

    return {"ok": True, "text": text, "usage": {"in": tin, "out": tout}, "cost": cost}


def cascade(task_text, system=None, temperature=None, on_event=None):
    """
    Parcourt la cascade cloud dans l'ordre jusqu'à un succès.
    on_event(msg) : callback optionnel pour journaliser les bascules.
    Retourne le 1er succès (dict enrichi de 'provider'), ou None si tout échoue.
    """
    providers = load_providers()
    for p in providers:
        res = call_provider(p, task_text, system=system, temperature=temperature)
        if res["ok"]:
            res["provider"] = p["name"]
            return res
        if on_event:
            on_event("cloud '%s' indisponible (%s) -> suivant"
                     % (p["name"], res.get("reason", "?")))
    return None


if __name__ == "__main__":
    # Diagnostic : affiche la cascade configurée (sans révéler les clés)
    ps = load_providers()
    if not ps:
        print("Aucun provider cloud configuré — mode 100% local.")
    else:
        print("Cascade cloud configurée (ordre de repli) :")
        for i, p in enumerate(ps, 1):
            keyhint = "clé ✓" if p["api_key"] else "sans clé"
            print("  %d. %s — %s (%s)" % (i, p["name"], p["model"], keyhint))
