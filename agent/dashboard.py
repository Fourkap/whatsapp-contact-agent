#!/usr/bin/env python3
"""UHD Coaching — dashboard + API REST du suivi clients WhatsApp (port 2786).

UI     : /            (clients, chat, analyses, paramètres)
Swagger: /docs        (doc interactive de l'API)
Spec   : /api/openapi.json

Si AGENT_API_KEY est défini, chaque appel /api/* doit porter le header X-API-Key.
Aucune dépendance externe (stdlib uniquement)."""
import json
import os
import re
import time
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs, quote

DATA = os.environ.get("DATA_DIR", "/data")
FICHES = os.path.join(DATA, "fiches")
DATADIR = os.path.join(DATA, "data")
PENDING = os.path.join(DATA, "_state", "pending")
ARCHIVE = os.path.join(DATA, "_state", "archive")
REQUESTS = os.path.join(DATA, "_state", "requests")
ANALYSES = os.path.join(DATA, "_state", "analyses")
PHONES = os.path.join(DATA, "_state", "phones.json")
LOG = os.path.join(DATA, "_state", "logs", "agent.log")
WHITELIST = os.path.join(DATA, "numeros.txt")
PORT = int(os.environ.get("DASHBOARD_PORT", "2786"))
API_KEY = os.environ.get("AGENT_API_KEY", "")
OPENWA_BASE = os.environ.get("OPENWA_BASE", "http://openwa-api:2785/api")
OPENWA_KEY_FILE = os.environ.get("OPENWA_KEY_FILE", "/openwa-data/.api-key")

# ---------- OpenWA proxy helpers ----------

_session_cache = {"id": None, "ts": 0}


def openwa_key():
    key = os.environ.get("OPENWA_API_KEY")
    if key:
        return key
    return open(OPENWA_KEY_FILE).read().strip()


def openwa(path, payload=None):
    req = urllib.request.Request(
        OPENWA_BASE + path,
        headers={"X-API-Key": openwa_key(), "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def session_id():
    if _session_cache["id"] and time.time() - _session_cache["ts"] < 60:
        return _session_cache["id"]
    ready = [s for s in openwa("/sessions") if s.get("status") == "ready"]
    if not ready:
        raise RuntimeError("aucune session WhatsApp connectée")
    _session_cache.update(id=ready[0]["id"], ts=time.time())
    return _session_cache["id"]


def slugify(name):
    s = re.sub(r"[^\w\s-]", "", name or "", flags=re.UNICODE).strip()
    return re.sub(r"[\s]+", "-", s) or "inconnu"


def read_json(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


# ---------- OpenAPI ----------

OPENAPI = {
    "openapi": "3.0.3",
    "info": {
        "title": "UHD Coaching — Agent API",
        "version": "2.0.0",
        "description": "API locale du suivi clients WhatsApp : fiches, data coaching "
                       "(sport / nutrition / santé / focus), chat, analyses Claude et paramètres.\n\n"
                       "Si `AGENT_API_KEY` est défini côté serveur, chaque requête doit porter "
                       "le header `X-API-Key`.",
    },
    "servers": [{"url": "/"}],
    "components": {
        "securitySchemes": {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
    },
    "security": [{"ApiKey": []}],
    "paths": {
        "/api/fiches": {"get": {"summary": "Liste des fiches clients + stats de synchro",
            "responses": {"200": {"description": "fiches[], archived, pending, lastSync"}}}},
        "/api/fiche/{nom}": {"get": {"summary": "Contenu Markdown d'une fiche",
            "parameters": [{"name": "nom", "in": "path", "required": True,
                            "schema": {"type": "string"}, "example": "Jean-Dupont.md"}],
            "responses": {"200": {"description": "Markdown"}, "404": {"description": "introuvable"}}}},
        "/api/data/{slug}": {"get": {"summary": "Data coaching structurée d'un client (sport, nutrition, santé, focus, mesures)",
            "parameters": [{"name": "slug", "in": "path", "required": True,
                            "schema": {"type": "string"}, "example": "Jean-Dupont"}],
            "responses": {"200": {"description": "JSON coaching"}, "404": {"description": "aucune data"}}}},
        "/api/contacts": {"get": {"summary": "Contacts connus (identifiant WhatsApp, numéro)",
            "responses": {"200": {"description": "contacts[], fiches[]"}}}},
        "/api/messages/{slug}": {"get": {"summary": "Messages bruts archivés d'un contact",
            "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 200}}],
            "responses": {"200": {"description": "messages[]"}}}},
        "/api/chats": {"get": {"summary": "Conversations WhatsApp en direct (via OpenWA)",
            "responses": {"200": {"description": "chats[]"}, "502": {"description": "OpenWA injoignable"}}}},
        "/api/chat/{chatId}/messages": {"get": {"summary": "Historique live d'une conversation",
            "parameters": [{"name": "chatId", "in": "path", "required": True,
                            "schema": {"type": "string"}, "example": "33612345678@c.us"},
                           {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}}],
            "responses": {"200": {"description": "messages[]"}}}},
        "/api/chat/{chatId}/send": {"post": {"summary": "Envoyer un message WhatsApp",
            "parameters": [{"name": "chatId", "in": "path", "required": True, "schema": {"type": "string"}}],
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "required": ["text"],
                           "properties": {"text": {"type": "string", "maxLength": 4096}}}}}},
            "responses": {"201": {"description": "messageId, timestamp"}}}},
        "/api/analyses": {
            "get": {"summary": "Analyses demandées (en attente + terminées)",
                    "responses": {"200": {"description": "analyses[]"}}},
            "post": {"summary": "Demander une analyse libre à Claude",
                     "requestBody": {"required": True, "content": {"application/json": {
                         "schema": {"type": "object", "required": ["question"],
                                    "properties": {"question": {"type": "string", "maxLength": 2000}}}}}},
                     "responses": {"202": {"description": "id, status, result_url"}}}},
        "/api/analyses/{id}": {"get": {"summary": "Résultat d'une analyse",
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "status + result (Markdown)"}}}},
        "/api/settings": {
            "get": {"summary": "Paramètres (liste de numéros suivis, config)",
                    "responses": {"200": {"description": "numeros[], config"}}},
            "post": {"summary": "Mettre à jour la liste de numéros suivis",
                     "requestBody": {"required": True, "content": {"application/json": {
                         "schema": {"type": "object", "required": ["numeros"],
                                    "properties": {"numeros": {"type": "array",
                                                               "items": {"type": "string"}}}}}}},
                     "responses": {"200": {"description": "ok"}}}},
    },
}

DOCS_PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>UHD Coaching — API</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<style>body{margin:0} .topbar{display:none}</style></head>
<body><div id="ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url:'/api/openapi.json',dom_id:'#ui',docExpansion:'list'})</script>
</body></html>"""

# ---------- UI ----------

PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UHD Coaching — Suivi clients</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,800;1,700&family=Montserrat:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0a; --panel: #131313; --panel2: #1a1a1a; --text: #f4f4f4; --muted: #9a9a9a;
    --accent: #ff0073; --accent-dim: #cc005c; --accent-soft: rgba(255,0,115,0.12);
    --border: #262626; --hover: #1f1f1f; --ok: #3ddc84; --warn: #ffb020;
    --serif: "Playfair Display", Georgia, serif;
    --sans: "Montserrat", -apple-system, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; }
  body { font: 14px/1.55 var(--sans); background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
  ::selection { background: var(--accent); color: #fff; }

  header { padding: 0 26px; background: var(--panel); border-top: 3px solid var(--accent); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 30px; height: 64px; }
  .logo { font-family: var(--serif); font-style: italic; font-weight: 800; font-size: 24px; letter-spacing: 0.01em; white-space: nowrap; }
  .logo b { color: var(--accent); }
  .tabs { display: flex; gap: 2px; height: 100%; }
  .tab { padding: 0 18px; height: 100%; display: flex; align-items: center; cursor: pointer; border: none; background: none;
         font-family: var(--sans); font-size: 12px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
         color: var(--muted); border-bottom: 2px solid transparent; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .stats { color: var(--muted); font-size: 12px; margin-left: auto; white-space: nowrap; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); margin-right: 6px; }

  main { flex: 1; display: flex; min-height: 0; }
  nav { width: 320px; min-width: 250px; border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; }
  nav input[type=search], nav textarea { margin: 14px 14px 0; padding: 10px 13px; border: 1px solid var(--border); border-radius: 4px;
    background: var(--bg); color: var(--text); font: 13px var(--sans); outline: none; resize: vertical; }
  nav input:focus, nav textarea:focus { border-color: var(--accent); }
  .btn { margin: 10px 14px 4px; padding: 11px; border: none; border-radius: 4px; background: var(--accent); color: #fff;
         font: 700 12px var(--sans); letter-spacing: 0.12em; text-transform: uppercase; cursor: pointer; }
  .btn:hover { background: var(--accent-dim); }
  .btn:disabled { opacity: 0.4; cursor: default; }
  #list { overflow-y: auto; flex: 1; margin-top: 10px; }
  .item { padding: 11px 16px; cursor: pointer; border-bottom: 1px solid var(--border); display: flex; gap: 12px; align-items: center; }
  .item:hover { background: var(--hover); }
  .item.active { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
  .av { width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, #2b2b2b, #191919); border: 1px solid var(--border);
        font-weight: 800; font-size: 12px; color: var(--muted); }
  .item.active .av, .item:hover .av { color: var(--accent); border-color: var(--accent-dim); }
  .item .meta { min-width: 0; flex: 1; }
  .item .n { font-weight: 600; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .item .d { color: var(--muted); font-size: 11.5px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sdot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: 1px; }
  .sdot.actif { background: var(--ok); } .sdot.relance { background: var(--warn); }
  .sdot.prospect { background: #4aa8ff; } .sdot.autre { background: #3a3a3a; }
  .pill { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; padding: 2px 8px; border-radius: 3px;
          background: var(--accent-soft); color: var(--accent); text-transform: uppercase; }
  .pill.wait { background: var(--panel2); color: var(--muted); }

  #view { flex: 1; overflow-y: auto; padding: 34px 44px; }
  .doc { max-width: 780px; }
  .doc h1 { font-family: var(--serif); font-size: 30px; font-weight: 800; margin-bottom: 16px; }
  .doc h2 { font-size: 12px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent); margin: 26px 0 8px; }
  .doc h3 { font-size: 14px; margin: 16px 0 6px; }
  .doc ul { padding-left: 20px; margin: 6px 0; }
  .doc li { margin: 4px 0; }
  .doc p { margin: 8px 0; }
  .doc strong { font-weight: 700; }
  .doc em { color: var(--muted); }
  .empty { color: var(--muted); text-align: center; margin-top: 90px; font-size: 13px; }

  /* dashboard client */
  .chead { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; max-width: 900px; }
  .chead h1 { font-family: var(--serif); font-size: 32px; font-weight: 800; }
  .badge { font-size: 10.5px; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; padding: 4px 10px; border-radius: 3px; }
  .badge.actif { background: rgba(61,220,132,0.14); color: var(--ok); }
  .badge.relance { background: rgba(255,176,32,0.14); color: var(--warn); }
  .badge.prospect { background: rgba(74,168,255,0.14); color: #4aa8ff; }
  .badge.autre { background: var(--panel2); color: var(--muted); }
  .chead .lc { color: var(--muted); font-size: 12.5px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 16px; max-width: 900px; }
  .tile { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px 18px 14px;
          display: flex; flex-direction: column; min-height: 116px; transition: border-color 0.15s; }
  .tile:hover { border-color: #3a3a3a; }
  .tile .tl { font-size: 10px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
  .tile .tv { font-family: var(--serif); font-size: 30px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }
  .tile .tv small { font-size: 14px; font-weight: 400; color: var(--muted); }
  .tile .delta { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .tile .delta b { color: var(--accent); font-weight: 700; }
  .tile svg { display: block; margin-top: auto; padding-top: 8px; width: 100%; }
  .two { display: grid; grid-template-columns: 1.2fr 1fr; gap: 14px; max-width: 900px; align-items: start; }
  @media (max-width: 900px) { .two { grid-template-columns: 1fr; } }
  details.fdoc { max-width: 900px; margin-top: 26px; border-top: 1px solid var(--border); padding-top: 14px; }
  details.fdoc summary { cursor: pointer; font-size: 11px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); }
  details.fdoc summary:hover { color: var(--text); }

  /* data coaching */
  .datagrid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 14px 0 8px; max-width: 900px; }
  @media (max-width: 900px) { .datagrid { grid-template-columns: 1fr; } }
  .dcard { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px 18px; transition: border-color 0.15s; }
  .dcard:hover { border-color: #3a3a3a; }
  .dcard h4 { font-size: 11px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent); margin-bottom: 10px;
              padding-bottom: 8px; border-bottom: 1px solid var(--border); }
  .dcard .entry { font-size: 13px; line-height: 1.5; padding: 7px 0; border-top: 1px solid var(--border); }
  .dcard .entry:first-of-type { border-top: none; }
  .dcard .entry time { color: var(--muted); font-size: 11px; display: block; margin-bottom: 1px; }
  .mesures { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }
  .mes { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px 18px; }
  .mes .v { font-family: var(--serif); font-size: 24px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .mes .l { font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); }

  /* chat */
  #chatview { display: flex; flex-direction: column; height: 100%; }
  #chatlog { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding-bottom: 16px; }
  .msg { max-width: 66%; padding: 9px 13px; border-radius: 8px; font-size: 13.5px; background: var(--panel2); align-self: flex-start; }
  .msg.me { background: var(--accent); color: #fff; align-self: flex-end; }
  .msg time { display: block; font-size: 10px; opacity: 0.65; margin-top: 3px; text-align: right; }
  #composer { display: flex; gap: 10px; padding-top: 14px; border-top: 1px solid var(--border); }
  #composer textarea { flex: 1; padding: 11px 14px; border: 1px solid var(--border); border-radius: 4px;
    background: var(--panel); color: var(--text); font: 13.5px var(--sans); outline: none; resize: none; }
  #composer textarea:focus { border-color: var(--accent); }
  #composer button { padding: 0 26px; border: none; border-radius: 4px; background: var(--accent); color: #fff;
    font: 700 12px var(--sans); letter-spacing: 0.1em; text-transform: uppercase; cursor: pointer; }

  /* paramètres */
  .set { max-width: 640px; }
  .set h1 { font-family: var(--serif); font-size: 28px; font-weight: 800; margin-bottom: 6px; }
  .set .hint { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .set textarea { width: 100%; min-height: 180px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 4px;
    background: var(--panel); color: var(--text); font: 13px/1.7 ui-monospace, Menlo, monospace; outline: none; }
  .set textarea:focus { border-color: var(--accent); }
  .set .row { display: flex; align-items: center; gap: 14px; margin-top: 12px; }
  .set .saved { color: var(--ok); font-size: 12.5px; }
  .cfg { margin-top: 34px; border-top: 1px solid var(--border); padding-top: 20px; }
  .cfg table { border-collapse: collapse; font-size: 13px; }
  .cfg td { padding: 6px 22px 6px 0; }
  .cfg td:first-child { color: var(--muted); }
  a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <div class="logo">UHD<b>.</b>COACHING</div>
  <div class="tabs">
    <button class="tab active" data-tab="clients">Clients</button>
    <button class="tab" data-tab="chat">Chat</button>
    <button class="tab" data-tab="analyses">Analyses</button>
    <button class="tab" data-tab="params">Paramètres</button>
  </div>
  <span class="stats" id="stats"><span class="dot"></span>chargement…</span>
</header>
<main>
  <nav id="sidebar">
    <input id="search" type="search" placeholder="Rechercher…">
    <textarea id="question" rows="3" placeholder="Question de coach… ex : quels clients relancer cette semaine ? qui a raté ses séances ?" style="display:none"></textarea>
    <button class="btn" id="ask" style="display:none">Analyser</button>
    <div id="list"></div>
  </nav>
  <div id="view"><div class="empty">Sélectionne un élément à gauche</div></div>
</main>
<script>
let tab = 'clients', fiches = [], analyses = [], chats = [], current = null, chatTimer = null;

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function md(src) {
  let out = [], inList = false;
  for (let line of src.split('\n')) {
    let h = esc(line)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|\s)\*(?!\s)(.+?)\*(?=\s|$|[.,;:])/g, '$1<em>$2</em>');
    if (/^# /.test(h)) { if(inList){out.push('</ul>');inList=false;} out.push('<h1>'+h.slice(2)+'</h1>'); }
    else if (/^## /.test(h)) { if(inList){out.push('</ul>');inList=false;} out.push('<h2>'+h.slice(3)+'</h2>'); }
    else if (/^### /.test(h)) { if(inList){out.push('</ul>');inList=false;} out.push('<h3>'+h.slice(4)+'</h3>'); }
    else if (/^[-*] /.test(h)) { if(!inList){out.push('<ul>');inList=true;} out.push('<li>'+h.slice(2)+'</li>'); }
    else if (h.trim()==='') { if(inList){out.push('</ul>');inList=false;} }
    else { if(inList){out.push('</ul>');inList=false;} out.push('<p>'+h+'</p>'); }
  }
  if (inList) out.push('</ul>');
  return out.join('');
}

async function jget(u) { const r = await fetch(u); if (!r.ok) throw new Error((await r.json()).error || r.status); return r.json(); }

function initials(name) {
  const parts = (name || '?').trim().split(/\s+/).filter(w => /[a-zA-ZÀ-ü]/.test(w[0]));
  return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function statutCls(st) {
  st = (st || '').toLowerCase();
  if (!st) return '';
  return st.includes('actif') ? 'actif' : st.includes('relanc') ? 'relance' : st.includes('prospect') ? 'prospect' : 'autre';
}

function spark(mesures) {
  const vals = mesures.map(m => m.valeur);
  if (vals.length < 2) return '';
  const w = 170, h = 42, p = 4;
  const min = Math.min(...vals), max = Math.max(...vals), r = (max - min) || 1;
  const pts = vals.map((v, i) => [p + i * (w - 2*p) / (vals.length - 1), h - p - (v - min) * (h - 2*p) / r]);
  const last = pts[pts.length - 1];
  return '<svg width="' + w + '" height="' + h + '" role="img" aria-label="évolution du poids">' +
    '<title>' + mesures.map(m => (m.date || '') + ' : ' + m.valeur).join('  ·  ') + '</title>' +
    '<polyline points="' + pts.map(pt => pt[0].toFixed(1) + ',' + pt[1].toFixed(1)).join(' ') +
    '" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
    '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="3" fill="var(--accent)"/></svg>';
}

async function load() {
  try {
    const [df, da] = await Promise.all([jget('/api/fiches'), jget('/api/analyses')]);
    fiches = df.fiches; analyses = da.analyses;
    document.getElementById('stats').innerHTML =
      '<span class="dot"></span>' + df.fiches.length + ' client(s) · ' + df.archived + ' msg · synchro ' + (df.lastSync ? df.lastSync.slice(11,16) : '—');
  } catch(e) {}
  if (tab === 'chat') { try { chats = (await jget('/api/chats')).chats; } catch(e) { chats = []; } }
  render();
}

function render() {
  const q = (document.getElementById('search').value || '').toLowerCase();
  const el = document.getElementById('list');
  document.getElementById('search').style.display = (tab === 'clients' || tab === 'chat') ? '' : 'none';
  document.getElementById('question').style.display = tab === 'analyses' ? '' : 'none';
  document.getElementById('ask').style.display = tab === 'analyses' ? '' : 'none';
  document.getElementById('sidebar').style.display = tab === 'params' ? 'none' : '';
  el.innerHTML = '';
  let items = [];
  if (tab === 'clients') items = fiches.filter(f => f.name.toLowerCase().includes(q)).map(f => ({
    key: f.file, title: f.name, sub: (f.statut ? esc(f.statut) + ' · ' : '') + 'maj ' + f.updated,
    dot: statutCls(f.statut), av: initials(f.name), done: true }));
  else if (tab === 'chat') items = chats.filter(c => (c.name||'').toLowerCase().includes(q)).map(c => ({
    key: c.id, title: c.name, sub: esc(c.lastMessage || ''), av: initials(c.name), done: true }));
  else if (tab === 'analyses') items = analyses.map(a => ({
    key: a.id, title: a.question, sub: esc(a.date), done: a.status === 'done' }));
  items.forEach(it => {
    const d = document.createElement('div');
    d.className = 'item' + (current === it.key ? ' active' : '');
    d.innerHTML = (it.av ? '<div class="av">' + esc(it.av) + '</div>' : '') +
      '<div class="meta"><div class="n">' + esc(it.title) + '</div><div class="d">' +
      (it.dot ? '<span class="sdot ' + it.dot + '"></span>' : '') + it.sub +
      (it.done ? '' : ' <span class="pill wait">en cours</span>') + '</div></div>';
    d.onclick = () => open(it.key);
    el.appendChild(d);
  });
  if (!items.length && tab !== 'params') el.innerHTML = '<div class="empty" style="margin-top:34px">' +
    ({clients:'Aucune fiche client', chat:'Aucune conversation', analyses:'Aucune analyse — pose une question ci-dessus'})[tab] + '</div>';
}

function stopChatPoll() { if (chatTimer) { clearInterval(chatTimer); chatTimer = null; } }

async function open(key) {
  current = key; render();
  const view = document.getElementById('view');
  stopChatPoll();
  if (tab === 'clients') {
    const slug = key.replace(/\.md$/, '');
    const [txt, data] = await Promise.all([
      fetch('/api/fiche/' + encodeURIComponent(key)).then(r => r.text()),
      fetch('/api/data/' + encodeURIComponent(slug)).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    if (!data) { view.innerHTML = '<div class="doc">' + md(txt) + '</div>'; return; }

    const cls = statutCls(data.statut) || 'autre';
    let html = '<div class="chead"><h1>' + esc(slug.replace(/-/g, ' ')) + '</h1>' +
      (data.statut ? '<span class="badge ' + cls + '">' + esc(data.statut) + '</span>' : '') +
      (data.dernierContact ? '<span class="lc">dernier contact : ' + esc(data.dernierContact) + '</span>' : '') + '</div>';

    let tiles = '';
    const poids = (data.mesures || []).filter(m => (m.type || '') === 'poids' && typeof m.valeur === 'number');
    if (poids.length) {
      const last = poids[poids.length - 1], delta = last.valeur - poids[0].valeur;
      tiles += '<div class="tile"><div class="tl">Poids · ' + esc(last.date || '') + '</div>' +
        '<div class="tv">' + last.valeur + '<small> ' + esc(last.unite || 'kg') + '</small></div>' +
        (poids.length > 1 ? '<div class="delta"><b>' + (delta > 0 ? '+' : '') + delta.toFixed(1) + ' ' + esc(last.unite || 'kg') + '</b> depuis le début</div>' + spark(poids) : '') + '</div>';
    }
    const j = data.dernierContact ? Math.max(0, Math.round((Date.now()/1000 - new Date(data.dernierContact).getTime()/1000) / 86400)) : null;
    if (j !== null) tiles += '<div class="tile"><div class="tl">Sans nouvelles</div><div class="tv">' + j + '<small> jour' + (j > 1 ? 's' : '') + '</small></div>' +
      '<div class="delta">' + (j >= 7 ? '<b>à relancer</b>' : 'contact récent') + '</div></div>';
    tiles += '<div class="tile"><div class="tl">Notes sport</div><div class="tv">' + (data.sport || []).length + '</div></div>';
    tiles += '<div class="tile"><div class="tl">À retenir</div><div class="tv">' + (data.intemporel || []).length + '<small> fait' + ((data.intemporel || []).length > 1 ? 's' : '') + '</small></div></div>';
    html += '<div class="tiles">' + tiles + '</div>';

    html += '<div class="two">' +
      '<div class="dcard"><h4>💬 Dernière conversation' + (data.dernierEchange && data.dernierEchange.date ? ' · ' + esc(data.dernierEchange.date) : '') + '</h4>' +
      '<div class="entry">' + esc((data.dernierEchange && data.dernierEchange.resume) || 'Pas encore de résumé') + '</div></div>' +
      '<div class="dcard"><h4>📌 À retenir</h4>' + ((data.intemporel || []).length
        ? data.intemporel.map(f => '<div class="entry">' + esc(f.note || '') + '</div>').join('')
        : '<div class="entry" style="color:var(--muted)">Rien de détecté pour l’instant</div>') + '</div></div>';

    const domains = [['sport','🏋️ Sport'],['nutrition','🍗 Nutrition'],['sante','❤️ Santé'],['focus','🧠 Focus']];
    html += '<div class="datagrid">' + domains.map(([k, label]) => {
      const entries = (data[k] || []).slice(-4).reverse();
      return '<div class="dcard"><h4>' + label + '</h4>' + (entries.length
        ? entries.map(e => '<div class="entry"><time>' + esc(e.date||'') + '</time>' + esc(e.note||'') + '</div>').join('')
        : '<div class="entry" style="color:var(--muted)">Rien d’extrait pour l’instant</div>') + '</div>';
    }).join('') + '</div>';

    html += '<details class="fdoc"><summary>Fiche complète ▾</summary><div class="doc">' + md(txt) + '</div></details>';
    view.innerHTML = html;
  } else if (tab === 'chat') {
    const c = chats.find(x => x.id === key);
    view.innerHTML = '<div id="chatview">' +
      '<div style="display:flex;align-items:center;gap:12px;padding-bottom:14px;border-bottom:1px solid var(--border);margin-bottom:12px">' +
      '<div class="av">' + esc(initials(c ? c.name : '?')) + '</div>' +
      '<div style="font-weight:700;font-size:15px">' + esc(c ? c.name : key) + '</div></div>' +
      '<div id="chatlog"><div class="empty">Chargement…</div></div>' +
      '<div id="composer"><textarea id="draft" rows="2" placeholder="Écrire au client…"></textarea><button id="send">Envoyer</button></div></div>';
    document.getElementById('send').onclick = sendMsg;
    document.getElementById('draft').addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendMsg();
    });
    await refreshChat(true);
    chatTimer = setInterval(() => refreshChat(false), 8000);
  } else if (tab === 'analyses') {
    const a = await jget('/api/analyses/' + encodeURIComponent(key));
    view.innerHTML = a.status === 'done' ? '<div class="doc">' + md(a.result) + '</div>'
      : '<div class="empty">⏳ Claude analyse… la réponse s’affichera ici automatiquement</div>';
  }
}

async function refreshChat(scroll) {
  if (tab !== 'chat' || !current) return;
  try {
    const d = await jget('/api/chat/' + encodeURIComponent(current) + '/messages?limit=50');
    const log = document.getElementById('chatlog');
    if (!log) return;
    log.innerHTML = d.messages.map(m =>
      '<div class="msg' + (m.fromMe ? ' me' : '') + '">' + esc(m.body || '[' + (m.type||'média') + ']') +
      '<time>' + new Date((m.timestamp||0)*1000).toLocaleString('fr-FR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) + '</time></div>').join('')
      || '<div class="empty">Aucun message</div>';
    if (scroll !== false) log.scrollTop = log.scrollHeight;
  } catch(e) {}
}

async function sendMsg() {
  const ta = document.getElementById('draft');
  const text = ta.value.trim();
  if (!text || !current) return;
  const btn = document.getElementById('send'); btn.disabled = true;
  try {
    const r = await fetch('/api/chat/' + encodeURIComponent(current) + '/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}) });
    if (r.ok) { ta.value = ''; await refreshChat(true); }
    else alert('Échec de l’envoi : ' + ((await r.json()).error || r.status));
  } finally { btn.disabled = false; }
}

async function showParams() {
  const view = document.getElementById('view');
  const s = await jget('/api/settings');
  view.innerHTML = '<div class="set"><h1>Paramètres</h1>' +
    '<div class="hint">Numéros suivis — un par ligne, avec ou sans indicatif. <strong>Liste vide = tous les contacts sont suivis.</strong> Appliqué à la prochaine synchro.</div>' +
    '<textarea id="nums">' + esc(s.numeros.join('\n')) + '</textarea>' +
    '<div class="row"><button class="btn" style="margin:0" id="savenums">Enregistrer</button><span class="saved" id="savedmsg"></span></div>' +
    '<div class="cfg"><h2 style="font-size:12px;letter-spacing:0.16em;text-transform:uppercase;color:var(--accent);margin-bottom:12px">Configuration</h2><table>' +
    '<tr><td>Synchronisation</td><td>toutes les ' + Math.round(s.config.intervalSeconds/60) + ' min</td></tr>' +
    '<tr><td>Fenêtre premier import</td><td>' + (s.config.initialWindowDays > 0 ? s.config.initialWindowDays + ' jours' : 'tout l’historique') + '</td></tr>' +
    '<tr><td>Messages max / conversation</td><td>' + s.config.historyLimit + '</td></tr>' +
    '<tr><td>API protégée par clé</td><td>' + (s.config.apiProtected ? 'oui (X-API-Key)' : 'non (accès local)') + '</td></tr>' +
    '<tr><td>Session WhatsApp</td><td>' + esc(s.config.session || 'non connectée') + '</td></tr>' +
    '<tr><td>Documentation API</td><td><a href="/docs" target="_blank">Swagger — /docs</a></td></tr>' +
    '</table></div></div>';
  document.getElementById('savenums').onclick = async () => {
    const numeros = document.getElementById('nums').value.split('\n').map(x => x.trim()).filter(Boolean);
    const r = await fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({numeros}) });
    document.getElementById('savedmsg').textContent = r.ok ? '✓ Enregistré — appliqué à la prochaine synchro' : 'Erreur';
    setTimeout(() => { const m = document.getElementById('savedmsg'); if (m) m.textContent = ''; }, 4000);
  };
}

document.getElementById('ask').onclick = async () => {
  const q = document.getElementById('question').value.trim();
  if (!q) return;
  const btn = document.getElementById('ask'); btn.disabled = true;
  await fetch('/api/analyses', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q}) });
  document.getElementById('question').value = ''; btn.disabled = false;
  await load();
};
document.querySelectorAll('.tab').forEach(t => t.onclick = async () => {
  tab = t.dataset.tab; current = null; stopChatPoll();
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
  document.getElementById('view').innerHTML = '<div class="empty">Sélectionne un élément à gauche</div>';
  if (tab === 'params') { render(); await showParams(); } else await load();
});
document.getElementById('search').addEventListener('input', render);
load();
setInterval(() => { if (tab !== 'chat' && tab !== 'params') load(); }, 30000);
</script>
</body>
</html>"""


def safe_md(name):
    return re.fullmatch(r"[\w\s.-]+\.md", name, flags=re.UNICODE) is not None


def safe_slug(s):
    return re.fullmatch(r"[\w-]+", s, flags=re.UNICODE) is not None


def safe_chat(s):
    return re.fullmatch(r"[\w.@-]+", s) is not None


def safe_id(s):
    return re.fullmatch(r"[a-f0-9-]{8,40}", s) is not None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def jsend(self, code, obj):
        return self.send(code, json.dumps(obj, ensure_ascii=False))

    def authorized(self):
        if not API_KEY:
            return True
        return self.headers.get("X-API-Key", "") == API_KEY

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length))

    # ---------------- GET ----------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if path == "/docs":
            return self.send(200, DOCS_PAGE, "text/html; charset=utf-8")
        if not path.startswith("/api/"):
            return self.jsend(404, {"error": "not found"})
        if path == "/api/openapi.json":
            return self.jsend(200, OPENAPI)
        if not self.authorized():
            return self.jsend(401, {"error": "X-API-Key manquant ou invalide"})

        try:
            return self.route_get(path, qs)
        except RuntimeError as e:
            return self.jsend(502, {"error": str(e)})
        except Exception as e:  # OpenWA down, etc.
            return self.jsend(502, {"error": f"erreur interne : {e}"})

    def route_get(self, path, qs):
        if path == "/api/fiches":
            fiches = []
            if os.path.isdir(FICHES):
                for f in sorted(os.listdir(FICHES)):
                    if not f.endswith(".md"):
                        continue
                    st = os.stat(os.path.join(FICHES, f))
                    statut = read_json(os.path.join(DATADIR, f[:-3] + ".json"), {}).get("statut")
                    fiches.append({"file": f, "name": f[:-3].replace("-", " "),
                                   "updated": time.strftime("%d/%m %H:%M", time.localtime(st.st_mtime)),
                                   "statut": statut, "mtime": st.st_mtime})
            fiches.sort(key=lambda x: -x["mtime"])
            archived = 0
            if os.path.isdir(ARCHIVE):
                for f in os.listdir(ARCHIVE):
                    try:
                        archived += sum(1 for _ in open(os.path.join(ARCHIVE, f)))
                    except OSError:
                        pass
            pending = len(os.listdir(PENDING)) if os.path.isdir(PENDING) else 0
            last_sync = None
            if os.path.exists(LOG):
                for line in reversed(open(LOG, errors="replace").readlines()[-200:]):
                    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) — synchronisation", line)
                    if m:
                        last_sync = m.group(1)
                        break
            return self.jsend(200, {"fiches": fiches, "archived": archived,
                                    "pending": pending, "lastSync": last_sync})

        if path.startswith("/api/fiche/"):
            name = path[len("/api/fiche/"):]
            full = os.path.join(FICHES, name)
            if safe_md(name) and os.path.isfile(full):
                return self.send(200, open(full, encoding="utf-8", errors="replace").read(),
                                 "text/markdown; charset=utf-8")
            return self.jsend(404, {"error": "fiche introuvable"})

        if path.startswith("/api/data/"):
            slug = path[len("/api/data/"):]
            if not safe_slug(slug):
                return self.jsend(400, {"error": "slug invalide"})
            full = os.path.join(DATADIR, slug + ".json")
            if os.path.isfile(full):
                return self.jsend(200, read_json(full, {}))
            return self.jsend(404, {"error": "aucune data pour ce client"})

        if path == "/api/contacts":
            phones = read_json(PHONES, {})
            fiches = {f[:-3] for f in os.listdir(FICHES) if f.endswith(".md")} if os.path.isdir(FICHES) else set()
            out = [{"chatId": cid, "phone": ph} for cid, ph in phones.items()]
            return self.jsend(200, {"contacts": out, "fiches": sorted(fiches)})

        if path.startswith("/api/messages/"):
            slug = path[len("/api/messages/"):]
            if not safe_slug(slug):
                return self.jsend(400, {"error": "slug invalide"})
            full = os.path.join(ARCHIVE, slug + ".jsonl")
            if not os.path.isfile(full):
                return self.jsend(404, {"error": "aucun message archivé pour ce contact"})
            try:
                limit = max(1, min(5000, int(qs.get("limit", ["200"])[0])))
            except ValueError:
                limit = 200
            lines = open(full, encoding="utf-8", errors="replace").readlines()[-limit:]
            msgs = [json.loads(l) for l in lines if l.strip()]
            return self.jsend(200, {"contact": slug, "count": len(msgs), "messages": msgs})

        if path == "/api/chats":
            sid = session_id()
            chats = openwa(f"/sessions/{sid}/chats?limit=1000")
            out = [{"id": c["id"], "name": c.get("name") or c["id"].split("@")[0],
                    "lastMessage": c.get("lastMessage"), "timestamp": c.get("timestamp"),
                    "unread": c.get("unreadCount", 0)}
                   for c in chats
                   if not c.get("isGroup") and (c["id"].endswith("@c.us") or c["id"].endswith("@lid"))]
            return self.jsend(200, {"chats": out})

        if path.startswith("/api/chat/") and path.endswith("/messages"):
            cid = path[len("/api/chat/"):-len("/messages")]
            if not safe_chat(cid):
                return self.jsend(400, {"error": "chatId invalide"})
            try:
                limit = max(1, min(100, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            sid = session_id()
            msgs = openwa(f"/sessions/{sid}/messages/{quote(cid)}/history?limit={limit}")
            msgs.sort(key=lambda m: m.get("timestamp") or 0)
            out = [{"id": m.get("id"), "body": m.get("body"), "type": m.get("type"),
                    "fromMe": bool(m.get("fromMe")), "timestamp": m.get("timestamp")} for m in msgs]
            return self.jsend(200, {"chatId": cid, "messages": out})

        if path == "/api/analyses":
            items = []
            if os.path.isdir(REQUESTS):
                for f in os.listdir(REQUESTS):
                    if f.endswith(".txt"):
                        p = os.path.join(REQUESTS, f)
                        items.append({"id": f[:-4], "question": open(p, encoding="utf-8").read().strip(),
                                      "status": "pending",
                                      "date": time.strftime("%d/%m %H:%M", time.localtime(os.stat(p).st_mtime)),
                                      "mtime": os.stat(p).st_mtime})
            if os.path.isdir(ANALYSES):
                for f in os.listdir(ANALYSES):
                    if f.endswith(".md"):
                        p = os.path.join(ANALYSES, f)
                        first = open(p, encoding="utf-8", errors="replace").readline().strip()
                        items.append({"id": f[:-3], "question": first.lstrip("# ") or f, "status": "done",
                                      "date": time.strftime("%d/%m %H:%M", time.localtime(os.stat(p).st_mtime)),
                                      "mtime": os.stat(p).st_mtime})
            items.sort(key=lambda x: -x["mtime"])
            return self.jsend(200, {"analyses": items})

        if path.startswith("/api/analyses/"):
            aid = path[len("/api/analyses/"):]
            if not safe_id(aid):
                return self.jsend(400, {"error": "id invalide"})
            done = os.path.join(ANALYSES, aid + ".md")
            if os.path.isfile(done):
                return self.jsend(200, {"id": aid, "status": "done",
                                        "result": open(done, encoding="utf-8", errors="replace").read()})
            if os.path.isfile(os.path.join(REQUESTS, aid + ".txt")):
                return self.jsend(200, {"id": aid, "status": "pending"})
            return self.jsend(404, {"error": "analyse introuvable"})

        if path == "/api/settings":
            numeros = []
            if os.path.exists(WHITELIST):
                for line in open(WHITELIST, encoding="utf-8"):
                    line = line.split("#")[0].strip()
                    if line:
                        numeros.append(line)
            session = None
            try:
                sid = session_id()
                session = f"connectée ({sid[:8]}…)"
            except Exception:
                pass
            return self.jsend(200, {"numeros": numeros, "config": {
                "intervalSeconds": int(os.environ.get("SYNC_INTERVAL_SECONDS", "1800")),
                "initialWindowDays": int(os.environ.get("INITIAL_WINDOW_DAYS", "30")),
                "historyLimit": int(os.environ.get("HISTORY_LIMIT", "100")),
                "apiProtected": bool(API_KEY),
                "session": session,
            }})

        return self.jsend(404, {"error": "not found"})

    # ---------------- POST ----------------
    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if not self.authorized():
            return self.jsend(401, {"error": "X-API-Key manquant ou invalide"})
        try:
            return self.route_post(path)
        except RuntimeError as e:
            return self.jsend(502, {"error": str(e)})
        except urllib.error.HTTPError as e:
            return self.jsend(502, {"error": f"OpenWA a répondu {e.code}"})
        except (ValueError, TypeError):
            return self.jsend(400, {"error": "corps JSON invalide"})
        except Exception as e:
            return self.jsend(502, {"error": f"erreur interne : {e}"})

    def route_post(self, path):
        if path == "/api/analyses":
            question = str(self.body_json().get("question", "")).strip()
            if not question or len(question) > 2000:
                return self.jsend(400, {"error": "question vide ou trop longue (max 2000 caractères)"})
            aid = uuid.uuid4().hex[:12]
            os.makedirs(REQUESTS, exist_ok=True)
            with open(os.path.join(REQUESTS, aid + ".txt"), "w", encoding="utf-8") as f:
                f.write(question)
            return self.jsend(202, {"id": aid, "status": "pending", "result_url": f"/api/analyses/{aid}"})

        if path.startswith("/api/chat/") and path.endswith("/send"):
            cid = path[len("/api/chat/"):-len("/send")]
            if not safe_chat(cid):
                return self.jsend(400, {"error": "chatId invalide"})
            text = str(self.body_json().get("text", "")).strip()
            if not text or len(text) > 4096:
                return self.jsend(400, {"error": "texte vide ou trop long (max 4096 caractères)"})
            sid = session_id()
            res = openwa(f"/sessions/{sid}/messages/send-text", {"chatId": cid, "text": text})
            return self.jsend(201, res)

        if path == "/api/settings":
            numeros = self.body_json().get("numeros", [])
            if not isinstance(numeros, list) or not all(isinstance(n, str) for n in numeros):
                return self.jsend(400, {"error": "numeros doit être une liste de chaînes"})
            clean = []
            for n in numeros:
                n = n.strip()
                if not n:
                    continue
                if not re.fullmatch(r"[+\d][\d\s.()-]{5,25}", n):
                    return self.jsend(400, {"error": f"numéro invalide : {n}"})
                clean.append(n)
            with open(WHITELIST, "w", encoding="utf-8") as f:
                f.write("# Numéros suivis — géré depuis le dashboard (Paramètres)\n")
                for n in clean:
                    f.write(n + "\n")
            return self.jsend(200, {"ok": True, "count": len(clean)})

        return self.jsend(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[dashboard] http://0.0.0.0:{PORT}" + (" (API protégée par clé)" if API_KEY else ""))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
