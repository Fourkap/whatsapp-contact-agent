# 📇 WhatsApp Contact Agent

Un agent qui maintient automatiquement une **fiche par contact WhatsApp** : qui est la personne, résumé de la relation, derniers échanges, choses à suivre. Les fiches sont rédigées et mises à jour par **Claude** (via ton abonnement Claude Pro/Max — pas de clé API à payer) et consultables dans un **dashboard web local**.

Tout tourne dans Docker, tout reste sur ta machine.

```
┌─────────────┐      ┌──────────────────────┐      ┌──────────────────┐
│  WhatsApp   │ ───► │  OpenWA (port 2785)  │ ───► │  contact-agent    │
│  (ton tél.) │  QR  │  API WhatsApp locale │ sync │  toutes les 30 min│
└─────────────┘      └──────────────────────┘      │  Claude ➜ fiches  │
                                                   │  Dashboard : 2786 │
                                                   └──────────────────┘
```

## Prérequis

- [Docker](https://www.docker.com/products/docker-desktop/)
- [Claude Code](https://claude.com/claude-code) + un abonnement Claude Pro ou Max
- Un compte WhatsApp sur ton téléphone

## Installation

### 1. Lancer OpenWA (la passerelle WhatsApp)

```bash
git clone https://github.com/rmyndharis/OpenWA.git
cd OpenWA
docker compose up -d
```

Récupère la clé API admin générée au premier démarrage :

```bash
docker exec openwa-api cat /app/data/.api-key
```

### 2. Connecter ton WhatsApp

Ouvre `http://localhost:2785`, connecte-toi avec la clé API, crée une session et scanne le QR code avec ton téléphone (WhatsApp → Réglages → Appareils connectés → Connecter un appareil).

### 3. Lancer l'agent

```bash
git clone https://github.com/Fourkap/whatsapp-contact-agent.git
cd whatsapp-contact-agent
cp .env.example .env
```

Remplis `.env` :

- `OPENWA_API_KEY` : la clé récupérée à l'étape 1
- `CLAUDE_CODE_OAUTH_TOKEN` : lance `claude setup-token` dans un terminal et colle le jeton `sk-ant-oat01-...`

Puis :

```bash
docker compose up -d
```

### 4. C'est tout ✅

- **Dashboard des fiches** : http://localhost:2786 (liste, recherche, contenu, état de la synchro)
- Les fiches sont aussi de simples fichiers Markdown dans `fiches/`
- Logs dans `_state/logs/` (`agent.log` pour la synchro, `claude.log` pour les résumés)

## Comment ça marche

Toutes les 30 minutes (`SYNC_INTERVAL_SECONDS`) :

1. `agent/sync.py` interroge OpenWA, détecte les nouveaux messages des conversations individuelles (les groupes sont ignorés) et les dépose dans `_state/pending/`
2. Si du nouveau est arrivé, Claude (modèle Sonnet, mode headless) lit les messages en attente et met à jour la fiche de chaque contact concerné selon `agent/prompt.md`
3. Les messages traités sont archivés dans `_state/archive/`

Au premier lancement, seules les conversations actives dans les **30 derniers jours** sont importées (100 messages max par conversation).

## Personnalisation

| Quoi | Où |
|---|---|
| Fréquence de synchro | `SYNC_INTERVAL_SECONDS` dans `.env` |
| Format / contenu des fiches | `agent/prompt.md` |
| Fenêtre du premier import | `INITIAL_WINDOW_DAYS` dans `agent/sync.py` |
| Inclure les groupes | filtre `isGroup` dans `agent/sync.py` |
| Modèle Claude | option `--model` dans `agent/run.sh` |

## Vie privée

⚠️ Tes messages et les fiches générées sont des **données personnelles**. Elles restent dans `fiches/` et `_state/` sur ta machine — ces dossiers sont exclus de git (`.gitignore`). Les nouveaux messages sont envoyés à l'API Claude d'Anthropic pour la rédaction des résumés, comme n'importe quelle conversation Claude.

## Licence

MIT
