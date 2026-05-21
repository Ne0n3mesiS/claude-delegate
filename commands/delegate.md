# delegate

Délègue une tâche à un modèle Ollama local (Qwen2.5:14b ou DeepSeek-R1:32b) pour économiser des tokens Claude.

## Usage

```
/delegate [--category CATEGORY] "ta tâche ici"
```

Catégories disponibles : `code`, `reasoning`, `longtext`, `fast`, `etl`

## Exécution

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/delegate.py" $ARGUMENTS
```
