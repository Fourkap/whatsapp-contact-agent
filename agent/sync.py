#!/usr/bin/env python3
"""Récupère les nouveaux messages WhatsApp depuis OpenWA et les dépose
dans _state/pending/<contact>.jsonl pour que l'agent Claude mette à jour les fiches."""
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.environ.get("OPENWA_BASE", "http://openwa-api:2785/api")
KEY_FILE = os.environ.get("OPENWA_KEY_FILE", "/openwa-data/.api-key")
DATA = os.environ.get("DATA_DIR", "/data")
STATE_FILE = os.path.join(DATA, "_state", "state.json")
PENDING_DIR = os.path.join(DATA, "_state", "pending")
INITIAL_WINDOW_DAYS = 30   # au premier passage, on ne remonte que 30 jours
HISTORY_LIMIT = 100        # messages max récupérés par conversation


def api(path):
    key = os.environ.get("OPENWA_API_KEY") or open(KEY_FILE).read().strip()
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def slug(name):
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    s = re.sub(r"[\s]+", "-", s)
    return s or "inconnu"


def main():
    sessions = api("/sessions")
    ready = [s for s in sessions if s.get("status") == "ready"]
    if not ready:
        print("Aucune session WhatsApp connectée — rien à faire.")
        return 0
    sid = ready[0]["id"]

    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))

    chats = api(f"/sessions/{sid}/chats?limit=1000")
    cutoff = time.time() - INITIAL_WINDOW_DAYS * 86400
    dirty = 0

    for chat in chats:
        cid = chat.get("id") or ""
        # conversations individuelles uniquement : @c.us (classique) ou @lid (nouveau format)
        if chat.get("isGroup"):
            continue
        if not (cid.endswith("@c.us") or cid.endswith("@lid")):
            continue
        last_ts = state.get(cid, 0)
        chat_ts = chat.get("timestamp") or 0
        # premier passage : on ignore les conversations inactives depuis 30 jours
        if last_ts == 0 and chat_ts < cutoff:
            continue
        if chat_ts <= last_ts:
            continue

        name = chat.get("name") or cid.split("@")[0]
        try:
            msgs = api(f"/sessions/{sid}/messages/{cid}/history?limit={HISTORY_LIMIT}")
        except Exception as e:
            print(f"  ! erreur historique {name}: {e}", file=sys.stderr)
            continue

        new = [m for m in msgs if (m.get("timestamp") or 0) > last_ts]
        if not new:
            # ne marquer "vu" que si la conversation est déjà connue : juste après la
            # connexion, l'historique peut être vide le temps que WhatsApp se synchronise
            if last_ts:
                state[cid] = chat_ts
            continue
        new.sort(key=lambda m: m.get("timestamp") or 0)

        out = os.path.join(PENDING_DIR, slug(name) + ".jsonl")
        with open(out, "a", encoding="utf-8") as f:
            for m in new:
                body = m.get("body") or f"[{m.get('type', 'media')}]"
                f.write(json.dumps({
                    "contact": name,
                    "phone": m.get("senderPhone") or cid.split("@")[0],
                    "de_moi": bool(m.get("fromMe")),
                    "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("timestamp") or 0)),
                    "type": m.get("type"),
                    "message": body[:2000],
                }, ensure_ascii=False) + "\n")

        state[cid] = max(chat_ts, new[-1].get("timestamp") or 0)
        dirty += 1
        print(f"  + {name}: {len(new)} nouveau(x) message(s)")

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w"))
    print(f"{dirty} contact(s) à mettre à jour.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
