#!/bin/sh
# Boucle principale de l'agent : sync OpenWA -> résumés Claude -> dodo 30 min.
set -u
INTERVAL="${SYNC_INTERVAL_SECONDS:-1800}"
DATA="${DATA_DIR:-/data}"
LOG="$DATA/_state/logs/agent.log"

mkdir -p "$DATA/_state/pending" "$DATA/_state/archive" "$DATA/_state/logs" "$DATA/fiches"

echo "[agent] démarrage — intervalle ${INTERVAL}s" | tee -a "$LOG"

# dashboard web local (port 2786)
python3 /agent/dashboard.py >> "$LOG" 2>&1 &

while true; do
  echo "[agent] $(date '+%Y-%m-%d %H:%M:%S') — synchronisation" >> "$LOG"
  python3 /agent/sync.py >> "$LOG" 2>&1

  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "[agent] pas de jeton Claude — résumés en pause (messages accumulés dans pending/)" >> "$LOG"
  elif [ -n "$(ls -A "$DATA/_state/pending" 2>/dev/null)" ]; then
    echo "[agent] nouveaux messages — mise à jour des fiches par Claude" >> "$LOG"
    PROMPT=$(sed "s/{{DATE}}/$(date '+%Y-%m-%d')/g" /agent/prompt.md)
    cd "$DATA" && claude -p "$PROMPT" \
      --model sonnet \
      --allowedTools "Read,Write,Edit,Glob,Grep" \
      --permission-mode acceptEdits \
      >> "$DATA/_state/logs/claude.log" 2>&1
    if [ $? -eq 0 ]; then
      # traité avec succès -> on archive les messages bruts
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

  sleep "$INTERVAL"
done
