#!/usr/bin/env bash
set -e

ALIAS_LINE="alias dg='python3 ~/claude-delegate/scripts/delegate.py'"
ZSHRC="$HOME/.zshrc"

if grep -qF "$ALIAS_LINE" "$ZSHRC" 2>/dev/null; then
  echo "✓ Alias 'dg' déjà présent dans $ZSHRC"
else
  echo "" >> "$ZSHRC"
  echo "# claude-delegate" >> "$ZSHRC"
  echo "$ALIAS_LINE" >> "$ZSHRC"
  echo "✓ Alias 'dg' ajouté dans $ZSHRC"
fi

echo ""
echo "Recharge ton shell avec :  source ~/.zshrc"
echo "Puis teste avec          :  dg \"ta tâche ici\""
