#!/bin/sh
# Boucle principale de l'agent :
#  - toutes les SYNC_INTERVAL_SECONDS : sync OpenWA -> mise à jour des fiches par Claude
#  - toutes les 20 s : traitement des analyses demandées via l'API (POST /api/analyses)
set -u
INTERVAL="${SYNC_INTERVAL_SECONDS:-1800}"
DATA="${DATA_DIR:-/data}"
LOG="$DATA/_state/logs/agent.log"

mkdir -p "$DATA/_state/pending" "$DATA/_state/archive" "$DATA/_state/logs" \
         "$DATA/_state/requests" "$DATA/_state/analyses" "$DATA/fiches"

echo "[agent] démarrage — intervalle ${INTERVAL}s" | tee -a "$LOG"

# dashboard web + API (port 2786)
python3 /agent/dashboard.py >> "$LOG" 2>&1 &

run_claude() {
  cd "$DATA" && claude -p "$1" \
    --model sonnet \
    --allowedTools "Read,Write,Edit,Glob,Grep" \
    --permission-mode acceptEdits \
    >> "$DATA/_state/logs/claude.log" 2>&1
}

NEXT_SYNC=0
while true; do
  NOW=$(date +%s)

  # ---- synchro périodique + mise à jour des fiches ----
  if [ "$NOW" -ge "$NEXT_SYNC" ]; then
    NEXT_SYNC=$((NOW + INTERVAL))
    echo "[agent] $(date '+%Y-%m-%d %H:%M:%S') — synchronisation" >> "$LOG"
    python3 /agent/sync.py >> "$LOG" 2>&1

    if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
      echo "[agent] pas de jeton Claude — résumés en pause (messages accumulés dans pending/)" >> "$LOG"
    elif [ -n "$(ls -A "$DATA/_state/pending" 2>/dev/null)" ]; then
      echo "[agent] nouveaux messages — mise à jour des fiches par Claude" >> "$LOG"
      if run_claude "$(sed "s/{{DATE}}/$(date '+%Y-%m-%d')/g" /agent/prompt.md)"; then
        for f in "$DATA/_state/pending/"*.jsonl; do
          [ -e "$f" ] || continue
          base=$(basename "$f" .jsonl)
          cat "$f" >> "$DATA/_state/archive/$base.jsonl"
          rm "$f"
        done
        echo "[agent] fiches mises à jour" >> "$LOG"
      else
        echo "[agent] ECHEC de Claude — les messages restent en attente (voir claude.log)" >> "$LOG"
      fi
    fi
  fi

  # ---- analyses à la demande (API) ----
  if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    for req in "$DATA/_state/requests/"*.txt; do
      [ -e "$req" ] || continue
      ID=$(basename "$req" .txt)
      QUESTION=$(cat "$req")
      echo "[agent] $(date '+%Y-%m-%d %H:%M:%S') — analyse $ID : $QUESTION" >> "$LOG"
      PROMPT="$(sed "s/{{DATE}}/$(date '+%Y-%m-%d')/g" /agent/analyze-prompt.md)

Question : $QUESTION

Écris ta réponse complète en Markdown dans le fichier _state/analyses/$ID.md (commence par un titre # reprenant la question)."
      if run_claude "$PROMPT" && [ -f "$DATA/_state/analyses/$ID.md" ]; then
        rm "$req"
        echo "[agent] analyse $ID terminée" >> "$LOG"
      else
        echo "# $QUESTION

⚠️ L'analyse a échoué — réessaie ou consulte _state/logs/claude.log" > "$DATA/_state/analyses/$ID.md"
        rm "$req"
        echo "[agent] ECHEC analyse $ID" >> "$LOG"
      fi
    done
  fi

  sleep 20
done
