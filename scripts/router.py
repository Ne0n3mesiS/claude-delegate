#!/usr/bin/env python3
"""
claude-delegate :: router.py
Le cerveau du plugin. Détecte les backends locaux (Ollama, LM Studio, vLLM,
llama.cpp, Jan) et choisit le meilleur modèle disponible pour une tâche donnée.

Aucune liste de modèles hardcodée : le routing se fait par PATTERNS de nom
(coder, r1, thinking, phi, llama...) appliqués à ce qui est réellement installé.
Ainsi le plugin reste pertinent quand de nouveaux modèles sortent.

Stdlib uniquement — aucune dépendance pip. Compatible Python 3.8+.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

# --------------------------------------------------------------------------- #
# Backends connus. Tous exposent une API compatible OpenAI (/v1/...).
# On teste chaque port; le premier qui répond gagne (ou tous, voir discover()).
# --------------------------------------------------------------------------- #
BACKENDS = [
    {"name": "ollama",    "base": "http://localhost:11434", "openai": "http://localhost:11434/v1", "tags": "/api/tags"},
    {"name": "lm-studio", "base": "http://localhost:1234",  "openai": "http://localhost:1234/v1",  "tags": None},
    {"name": "vllm",      "base": "http://localhost:8000",  "openai": "http://localhost:8000/v1",  "tags": None},
    {"name": "llamacpp",  "base": "http://localhost:8080",  "openai": "http://localhost:8080/v1",  "tags": None},
    {"name": "jan",       "base": "http://localhost:1337",  "openai": "http://localhost:1337/v1",  "tags": None},
]

# Permet de forcer un backend/URL via env si l'utilisateur a une config custom
ENV_BASE = os.environ.get("DELEGATE_BACKEND_URL")  # ex: http://localhost:11434
ENV_BACKEND_NAME = os.environ.get("DELEGATE_BACKEND_NAME", "custom")

CONNECT_TIMEOUT = 1.5   # détection rapide d'un port
GEN_TIMEOUT = 600       # une génération locale peut être longue

# --------------------------------------------------------------------------- #
# Matrice de connaissance : catégorie de tâche -> patterns de noms de modèles,
# par ordre de préférence. Basé sur les benchmarks 2026 (HumanEval, MATH,
# LiveCodeBench). On matche sur des FAMILLES, pas des versions précises.
# --------------------------------------------------------------------------- #
TASK_PROFILES = {
    "code": {
        "keywords": ["écris une fonction", "write a function", "implémente", "implement",
                     "refactor", "refactore", "script", "classe ", "class ", "endpoint",
                     "regex", "requête sql", "sql query", "compile", "unit test",
                     "test unitaire", "génère le code", "generate code", "code python",
                     "code js", "fonction qui"],
        # générer/écrire du code : modèles "coder" en tête
        "prefer": [r"coder", r"qwen.*cod", r"deepseek.*cod", r"codellama", r"starcoder",
                   r"qwen", r"deepseek", r"llama", r"gemma", r"mistral"],
    },
    "reasoning": {
        "keywords": ["pourquoi", "why", "debug", "déboggue", "diagnostique", "diagnose",
                     "root cause", "cause racine", "analyse", "analyze", "raisonne",
                     "reason", "compare", "logique", "logic", "prouve", "prove",
                     "étape par étape", "step by step", "math", "démontre", "explique pourquoi",
                     "explain why", "trouve l'erreur", "find the bug", "qu'est-ce qui ne va pas"],
        # raisonnement/debug : modèles "thinking/r1/phi-4" en tête
        "prefer": [r"r1", r"reason", r"think", r"qwq", r"phi-?4", r"phi4",
                   r"deepseek", r"qwen", r"llama", r"mistral"],
    },
    "longtext": {
        "keywords": ["rédige", "write", "génère", "generate", "résume", "summarize",
                     "traduis", "translate", "article", "rapport", "report",
                     "documentation", "readme", "blog", "liste", "list", "questions",
                     "résumé", "synthèse", "synthesize", "réécris", "rewrite",
                     "draft", "brouillon", "explique", "explain", "décris", "describe"],
        # rédaction : on ÉVITE les modèles "coder" (négatif), on préfère généralistes
        "avoid": [r"coder", r"cod\b", r"starcoder", r"codellama"],
        "prefer": [r"qwen", r"llama", r"gemma", r"mistral", r"phi", r"deepseek"],
    },
    "fast": {
        "keywords": ["extrait", "extract", "classe le", "classify", "tag", "json",
                     "parse", "convertis", "convert", "formate", "format", "renomme",
                     "rename", "oui ou non", "yes or no", "vrai ou faux", "true or false",
                     "en une ligne", "one line", "rapidement", "quickly"],
        "prefer": [r"3b", r"1\.?5b", r"7b", r"8b", r"mini", r"small", r"phi", r"qwen",
                   r"llama", r"gemma"],
    },
    "etl": {
        # Transformation données structurées -> texte narratif/sémantique.
        # Ni code, ni raisonnement libre : synthèse déterministe de données.
        "keywords": ["transforme", "transform", "fiche narrative", "synthétise cette ligne",
                     "csv vers", "csv to", "structure ces données", "structure this data",
                     "etl", "ingère", "ingest", "normalise", "normalize", "sémantique",
                     "semantic", "enrichis cette donnée", "enrich", "ligne en fiche",
                     "row to", "données en texte", "data to text"],
        # synthèse données->texte : généraliste solide, on évite les coder
        "avoid": [r"coder", r"starcoder", r"codellama"],
        "prefer": [r"qwen", r"llama", r"mistral", r"gemma", r"deepseek"],
        # température basse par défaut : sortie reproductible pour pipelines
        "temperature": 0.2,
    },
}

DEFAULT_CATEGORY = "longtext"

# Température par défaut quand non spécifiée par la catégorie ou l'utilisateur
DEFAULT_TEMPERATURE = 0.7

# Taille (paramètres) extraite du nom -> pour départager / préférer petit en "fast"
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _http_json(url, timeout=CONNECT_TIMEOUT, data=None, headers=None):
    """GET ou POST JSON minimaliste via stdlib. Retourne dict ou lève."""
    req = urllib.request.Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _list_models(backend):
    """Retourne la liste des noms de modèles dispo sur un backend, ou []."""
    # Ollama a un endpoint natif /api/tags plus fiable
    if backend.get("tags"):
        try:
            d = _http_json(backend["base"] + backend["tags"])
            return [m["name"] for m in d.get("models", [])]
        except Exception:
            pass
    # Sinon endpoint OpenAI /v1/models
    try:
        d = _http_json(backend["openai"] + "/models")
        return [m["id"] for m in d.get("data", [])]
    except Exception:
        return []


def discover():
    """
    Détecte tous les backends actifs et leurs modèles.
    Retourne une liste : [{name, openai, models:[...]}, ...]
    """
    found = []
    candidates = list(BACKENDS)
    if ENV_BASE:
        candidates.insert(0, {
            "name": ENV_BACKEND_NAME,
            "base": ENV_BASE,
            "openai": ENV_BASE.rstrip("/") + "/v1",
            "tags": ENV_BASE.rstrip("/") + "/api/tags",
        })
    for b in candidates:
        models = _list_models(b)
        if models:
            found.append({"name": b["name"], "openai": b["openai"], "models": models})
    return found


def categorize(task_text):
    """Devine la catégorie de tâche à partir du texte. Renvoie (cat, score_map).

    Le scoring est pondéré : un mot-clé long/spécifique ("step by step") vaut
    plus qu'un mot court/ambigu. 'reasoning' a un léger bonus car ses signaux
    (debug, pourquoi, analyse) sont souvent noyés sous des mots génériques.
    """
    t = task_text.lower()
    # priorité de départage si égalité : etl > reasoning > code > fast > longtext
    tiebreak = {"etl": 4, "reasoning": 3, "code": 2, "fast": 1, "longtext": 0}
    scores = {}
    for cat, prof in TASK_PROFILES.items():
        s = 0.0
        for kw in prof["keywords"]:
            if kw in t:
                # mots-clés multi-mots = signal plus fort
                s += 1.5 if " " in kw else 1.0
        if cat == "reasoning":
            s *= 1.2  # léger bonus : ces signaux sont souvent sous-représentés
        scores[cat] = round(s, 2)
    best = max(scores, key=lambda c: (scores[c], tiebreak[c]))
    if scores[best] == 0:
        best = DEFAULT_CATEGORY
    return best, scores


def _model_size(name):
    m = SIZE_RE.search(name)
    return float(m.group(1)) if m else None


def pick_model(task_text, available, category=None):
    """
    Choisit (backend, model) le plus adapté.
    available = sortie de discover().
    """
    if not available:
        return None, None, None

    if category is None:
        category, _ = categorize(task_text)
    profile = TASK_PROFILES.get(category, TASK_PROFILES[DEFAULT_CATEGORY])
    patterns = profile["prefer"]
    avoid = profile.get("avoid", [])

    # Aplatis (backend, model_name) tout en gardant la provenance
    flat = []
    for b in available:
        for mname in b["models"]:
            flat.append((b, mname))

    # Score chaque modèle : rang du premier pattern qui matche (plus bas = mieux)
    def score(entry):
        _, name = entry
        n = name.lower()
        # pénalité si le modèle correspond à un pattern "avoid"
        penalty = 100 if any(re.search(p, n) for p in avoid) else 0
        rank = len(patterns) + 1
        for i, pat in enumerate(patterns):
            if re.search(pat, n):
                rank = i
                break
        size = _model_size(name)
        # En "fast", on préfère petit ; sinon on préfère gros (à pattern égal)
        if category == "fast":
            size_key = size if size is not None else 999
        else:
            size_key = -(size if size is not None else 0)
        return (penalty + rank, size_key)

    flat.sort(key=score)
    best_backend, best_model = flat[0]
    return best_backend["name"], best_model, category, best_backend["openai"]


def category_temperature(category):
    """Température recommandée pour une catégorie (sinon défaut global)."""
    prof = TASK_PROFILES.get(category, {})
    return prof.get("temperature", DEFAULT_TEMPERATURE)


def generate(openai_base, model, task_text, system=None, temperature=None):
    """Appelle l'endpoint /chat/completions compatible OpenAI. Retourne le texte."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task_text})
    payload = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    # clé bidon : les serveurs locaux l'ignorent mais certains la veulent présente
    headers = {"Authorization": "Bearer local-no-key"}
    d = _http_json(openai_base + "/chat/completions", timeout=GEN_TIMEOUT,
                   data=payload, headers=headers)
    return d["choices"][0]["message"]["content"]


# Estimation grossière : ~4 caractères par token (anglais/français mélangés)
def estimate_tokens(text):
    return max(1, round(len(text) / 4))


if __name__ == "__main__":
    # Petit CLI de test : python3 router.py "ma tâche"
    task = " ".join(sys.argv[1:]) or "écris une fonction python de tri rapide"
    avail = discover()
    if not avail:
        print(json.dumps({"error": "no_backend",
                          "msg": "Aucun backend local détecté (Ollama/LM Studio/vLLM)."}))
        sys.exit(1)
    name, model, cat, base = pick_model(task, avail)
    print(json.dumps({"backend": name, "model": model, "category": cat,
                      "available": [{b['name']: b['models']} for b in avail]},
                     ensure_ascii=False, indent=2))
