# claude-delegate

**Délègue automatiquement les tâches gourmandes en tokens à vos modèles locaux.**
Économisez des tokens Claude réels en routant la génération de volume, le parsing,
les résumés et le code répétitif vers Ollama, LM Studio, vLLM, llama.cpp ou Jan —
sans configuration de modèle, et sans casser votre workflow.

```
┌─ Claude Code ──────────────┐      ┌─ Modèle local ─────────┐
│ Tâche lourde détectée      │ ───▶ │ Ollama / LM Studio /   │
│ /delegate "..."  ou  dg    │      │ vLLM / llama.cpp / Jan │
│ Décisions, debug, archi    │ ◀─── │ Génère (0 token Claude)│
└────────────────────────────┘      └────────────────────────┘
```

## Pourquoi

Les modèles locaux sont excellents pour les sous-tâches structurées (génération,
parsing, résumés, boilerplate). Claude reste le meilleur pour l'architecture, le
debug multi-fichiers et les arbitrages. `claude-delegate` envoie chaque tâche au
bon endroit et **mesure les tokens réellement économisés**.

## Ce qui rend ce plugin différent

- **Aucune liste de modèles hardcodée.** Le routeur interroge `ollama list`
  (et les endpoints OpenAI-compatibles) et choisit le meilleur modèle *installé*
  par patterns de nom (`coder`, `r1`, `thinking`, `phi`, `llama`…). Il reste
  pertinent quand de nouveaux modèles sortent.
- **Multi-backend.** Détecte automatiquement Ollama (11434), LM Studio (1234),
  vLLM (8000), llama.cpp (8080) et Jan (1337). Tous via API OpenAI-compatible.
- **Routing par type de tâche.** `code` → modèles *coder* ; `reasoning` → *r1/phi-4* ;
  `longtext` → généralistes (évite les *coder*) ; `fast` → petit modèle rapide.
- **Économie mesurée, pas promise.** Chaque délégation est journalisée ;
  `/delegate-stats` affiche le cumul réel.
- **Zéro dépendance.** Python standard library uniquement.

## Installation

### Comme plugin Claude Code

```
/plugin marketplace add edgarmac/claude-delegate
/plugin install claude-delegate
```

Vous obtenez les commandes `/delegate`, `/delegate-stats`, `/delegate-doctor`
et un hook discret qui repère les commandes déléguables.

### Alias shell global `dg` (optionnel mais recommandé)

```
git clone https://github.com/edgarmac/claude-delegate
cd claude-delegate && bash install.sh
source ~/.zshrc   # ou ~/.bashrc
```

## Usage

```bash
# Délégation directe (routing automatique)
dg "écris 30 questions-réponses sur le Code civil"
dg "résume ce fichier en 10 points"

# Forcer une catégorie
python3 scripts/delegate.py --category reasoning "pourquoi ce test échoue ?"

# Voir le routing sans générer
dg --dry-run "implémente un cache LRU"

# Dans Claude Code
/delegate "génère un jeu de données CSV de 200 lignes"
/delegate-doctor      # quels backends/modèles sont détectés ?
/delegate-stats       # combien de tokens économisés jusqu'ici ?
```

## Matrice de routing

| Catégorie  | Signaux                              | Modèles préférés (par nom)        |
|------------|--------------------------------------|-----------------------------------|
| `code`     | « écris une fonction », implémente    | *coder*, qwen, deepseek, codellama|
| `reasoning`| pourquoi, debug, analyse, étape par étape | *r1*, *thinking*, qwq, phi-4   |
| `longtext` | rédige, résume, traduis, article      | qwen, llama, gemma (évite *coder*)|
| `fast`     | extrait, json, parse, oui/non         | plus petit modèle dispo (3b/7b)   |
| `etl`      | transforme, fiche narrative, structure, sémantique | qwen, llama (évite *coder*), **T=0.2** |

### Catégorie `etl` — synthèse de données déterministe

Pensée pour les pipelines qui transforment des données structurées (CSV, lignes
de base, enregistrements) en texte narratif/sémantique — typiquement pour
alimenter une base vectorielle. Elle force une **température basse (0.2)** par
défaut pour des sorties reproductibles, et évite les modèles *coder* au profit
de généralistes solides.

```bash
# Transformer une ligne en fiche, sortie déterministe
dg --category etl "transforme cette ligne SIRENE en fiche narrative : 12345;LVMH;luxe;PME"

# Forcer le déterminisme total
python3 scripts/delegate.py --category etl --temperature 0.0 "structure ces données : ..."
```

> Bonne pratique : gardez la connaissance métier (quel format CSV, quelles
> colonnes, quel gabarit de fiche) dans **votre** code, et passez à
> `claude-delegate` un prompt déjà formaté. Le plugin route et mesure ;
> il n'a pas à connaître votre schéma de données.

## Configuration (variables d'environnement)

| Variable                   | Effet                                                  |
|----------------------------|--------------------------------------------------------|
| `DELEGATE_BACKEND_URL`     | Force un backend custom (ex. serveur distant)          |
| `DELEGATE_BACKEND_NAME`    | Nom affiché du backend custom                          |
| `DELEGATE_PRICE_PER_MTOK`  | Prix de référence ($/Mtok) pour l'estimation d'économie|
| `DELEGATE_PROVIDERS`       | Chemin custom vers le fichier de cascade cloud         |

## Cascade de repli (local → local → cloud)

Par défaut, claude-delegate est **100% local et gratuit**. Mais si votre modèle
local échoue ou sature, le plugin sait basculer — sans jamais vous coûter quoi
que ce soit tant que vous restez sur des tiers gratuits.

L'ordre de repli :

```
1. Modèle local principal (le mieux routé)
2. Autre modèle local installé          ← si le 1er échoue
3. Cascade cloud, dans l'ordre          ← si aucun local ne répond
   ├─ provider gratuit #1
   ├─ provider gratuit #2  ← si #1 retourne 429 (quota épuisé) ou 503
   └─ ...
```

**Le cloud est entièrement optionnel.** Sans configuration, les étapes 3+ sont
simplement ignorées. Pour l'activer, copiez l'exemple :

```bash
cp providers.example.json ~/.claude/delegate/providers.json
export GEMINI_API_KEY="votre-clé-gratuite"
export GROQ_API_KEY="votre-clé-gratuite"
```

Le format est **générique** : n'importe quel endpoint compatible OpenAI marche
(Gemini, Groq, OpenRouter, Cerebras, Mistral…). Vous ajoutez un provider en
quelques lignes, sans toucher au code :

```json
{
  "providers": [
    { "name": "gemini-free",
      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
      "api_key": "env:GEMINI_API_KEY",
      "model": "gemini-2.5-flash",
      "price_per_mtok_in": 0, "price_per_mtok_out": 0 }
  ]
}
```

`"env:NOM"` lit la clé depuis une variable d'environnement (recommandé) plutôt
que de l'écrire en clair. Sur **quota gratuit épuisé** (HTTP 429) ou **saturation**
(503), le plugin passe automatiquement au provider suivant — la rotation sur
tiers gratuits permet de tourner en continu à coût nul.

Chaque délégation est journalisée avec son **tier** (local/cloud), la **route**
réellement parcourue, et le **coût réel** (0 en local et sur tiers gratuits).
`/delegate-doctor` affiche la cascade configurée.

## Limites (honnêtes)

- Un hook ne peut pas lire l'« intention » de Claude avant qu'il agisse ; il
  observe les commandes. La délégation reste donc déclenchée par vous
  (`/delegate`, `dg`) ou suggérée par le hook — jamais forcée silencieusement.
- L'estimation de tokens est approximative (~4 caractères/token).
- La qualité dépend du modèle local installé. Pour le code et le raisonnement
  exigeants, Claude reste souvent supérieur — déléguez le volume, gardez le subtil.

## Licence

MIT.
