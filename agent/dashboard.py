#!/usr/bin/env python3
"""Dashboard web + API REST des fiches contacts WhatsApp (port 2786).

Endpoints :
  GET  /api/fiches             — fiches + stats de synchro
  GET  /api/fiche/<Nom.md>     — contenu Markdown d'une fiche
  GET  /api/contacts           — contacts connus (nom, numéro, fiche ?)
  GET  /api/messages/<slug>    — messages bruts archivés (?limit=200)
  GET  /api/analyses           — analyses demandées (en attente + terminées)
  GET  /api/analyses/<id>      — résultat d'une analyse
  POST /api/analyses {question}— demander une analyse à Claude

Si AGENT_API_KEY est défini, chaque appel /api/* doit porter le header X-API-Key.
Aucune dépendance externe."""
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs

DATA = os.environ.get("DATA_DIR", "/data")
FICHES = os.path.join(DATA, "fiches")
PENDING = os.path.join(DATA, "_state", "pending")
ARCHIVE = os.path.join(DATA, "_state", "archive")
REQUESTS = os.path.join(DATA, "_state", "requests")
ANALYSES = os.path.join(DATA, "_state", "analyses")
PHONES = os.path.join(DATA, "_state", "phones.json")
LOG = os.path.join(DATA, "_state", "logs", "agent.log")
PORT = int(os.environ.get("DASHBOARD_PORT", "2786"))
API_KEY = os.environ.get("AGENT_API_KEY", "")

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UHD Coaching — Suivi clients</title>
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --text: #1c2230; --muted: #6b7385;
    --accent: #1f7a54; --accent-soft: #e3f2ea; --border: #e3e6ec; --hover: #eef0f4;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14171e; --panel: #1d222c; --text: #e8eaf0; --muted: #8b93a7;
      --accent: #4ecb96; --accent-soft: #1e3a2d; --border: #2a3140; --hover: #252b38;
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body { font: 15px/1.55 -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
  header { padding: 12px 22px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 17px; font-weight: 650; }
  header .tabs { display: flex; gap: 4px; }
  header .tab { padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 550; color: var(--muted); border: none; background: none; }
  header .tab.active { background: var(--accent-soft); color: var(--accent); }
  header .stats { color: var(--muted); font-size: 13px; margin-left: auto; }
  header .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); margin-right: 5px; }
  main { flex: 1; display: flex; min-height: 0; }
  nav { width: 300px; min-width: 230px; border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; }
  nav input, nav textarea { margin: 12px 12px 0; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font: inherit; font-size: 14px; outline: none; resize: vertical; }
  nav input:focus, nav textarea:focus { border-color: var(--accent); }
  nav button { margin: 8px 12px 4px; padding: 9px; border: none; border-radius: 8px; background: var(--accent); color: #fff; font-weight: 600; font-size: 14px; cursor: pointer; }
  nav button:disabled { opacity: 0.5; }
  #list { overflow-y: auto; flex: 1; margin-top: 8px; }
  .item { padding: 11px 16px; cursor: pointer; border-bottom: 1px solid var(--border); }
  .item:hover { background: var(--hover); }
  .item.active { background: var(--accent-soft); border-left: 3px solid var(--accent); padding-left: 13px; }
  .item .n { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .item .d { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .pill { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); }
  .pill.wait { background: var(--hover); color: var(--muted); }
  #view { flex: 1; overflow-y: auto; padding: 30px 40px; max-width: 880px; }
  #view h1 { font-size: 23px; margin-bottom: 14px; }
  #view h2 { font-size: 16px; margin: 22px 0 8px; color: var(--accent); }
  #view h3 { font-size: 15px; margin: 16px 0 6px; }
  #view ul { padding-left: 22px; margin: 6px 0; }
  #view li { margin: 3px 0; }
  #view p { margin: 7px 0; }
  #view strong { font-weight: 650; }
  #view em { color: var(--muted); }
  .empty { color: var(--muted); text-align: center; margin-top: 80px; }
  @media (max-width: 700px) { nav { width: 170px; } #view { padding: 18px; } }
</style>
</head>
<body>
<header>
  <h1>🏋️ UHD Coaching</h1>
  <div class="tabs">
    <button class="tab active" data-tab="fiches">Fiches</button>
    <button class="tab" data-tab="analyses">Analyses</button>
  </div>
  <span class="stats" id="stats"><span class="dot"></span>chargement…</span>
</header>
<main>
  <nav>
    <input id="search" type="search" placeholder="Rechercher un client…">
    <textarea id="question" rows="3" placeholder="Pose une question de coach… ex : quels clients relancer cette semaine ? qui a raté ses séances ?" style="display:none"></textarea>
    <button id="ask" style="display:none">Analyser</button>
    <div id="list"></div>
  </nav>
  <div id="view"><div class="empty">Sélectionne un élément à gauche</div></div>
</main>
<script>
let tab = 'fiches', fiches = [], analyses = [], current = null;

function md(src) {
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  let out = [], inList = false;
  for (let line of src.split('\\n')) {
    let h = esc(line)
      .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
      .replace(/(^|\\s)\\*(?!\\s)(.+?)\\*(?=\\s|$|[.,;:])/g, '$1<em>$2</em>');
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

async function load() {
  const [rf, ra] = await Promise.all([fetch('/api/fiches'), fetch('/api/analyses')]);
  const df = await rf.json(); analyses = (await ra.json()).analyses;
  fiches = df.fiches;
  document.getElementById('stats').innerHTML =
    '<span class="dot"></span>' + df.fiches.length + ' fiche(s) · ' + df.archived + ' msg traités · synchro : ' + (df.lastSync || '—');
  render();
}

function render() {
  const q = document.getElementById('search').value.toLowerCase();
  const el = document.getElementById('list');
  el.innerHTML = '';
  document.getElementById('search').style.display = tab === 'fiches' ? '' : 'none';
  document.getElementById('question').style.display = tab === 'analyses' ? '' : 'none';
  document.getElementById('ask').style.display = tab === 'analyses' ? '' : 'none';
  const items = tab === 'fiches'
    ? fiches.filter(f => f.name.toLowerCase().includes(q)).map(f => ({
        key: f.file, title: f.name, sub: 'maj ' + f.updated, done: true, kind: 'fiche' }))
    : analyses.map(a => ({
        key: a.id, title: a.question, sub: a.date, done: a.status === 'done', kind: 'analyse' }));
  items.forEach(it => {
    const d = document.createElement('div');
    d.className = 'item' + (current === it.key ? ' active' : '');
    d.innerHTML = '<div class="n">' + it.title + '</div><div class="d">' + it.sub +
      (it.done ? '' : ' <span class="pill wait">en cours…</span>') + '</div>';
    d.onclick = () => open(it);
    el.appendChild(d);
  });
  if (!items.length) el.innerHTML = '<div class="empty" style="margin-top:30px">' +
    (tab === 'fiches' ? 'Aucune fiche client' : 'Aucune analyse — pose une question ci-dessus') + '</div>';
}

async function open(it) {
  current = it.key; render();
  const view = document.getElementById('view');
  if (it.kind === 'fiche') {
    const r = await fetch('/api/fiche/' + encodeURIComponent(it.key));
    view.innerHTML = md(await r.text());
  } else {
    const r = await fetch('/api/analyses/' + encodeURIComponent(it.key));
    const a = await r.json();
    view.innerHTML = a.status === 'done' ? md(a.result)
      : '<div class="empty">⏳ Claude analyse… (la réponse apparaît ici, actualisation automatique)</div>';
  }
}

document.getElementById('ask').onclick = async () => {
  const q = document.getElementById('question').value.trim();
  if (!q) return;
  const btn = document.getElementById('ask'); btn.disabled = true;
  await fetch('/api/analyses', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q}) });
  document.getElementById('question').value = ''; btn.disabled = false;
  await load();
};
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  tab = t.dataset.tab; current = null;
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
  document.getElementById('view').innerHTML = '<div class="empty">Sélectionne un élément à gauche</div>';
  render();
});
document.getElementById('search').addEventListener('input', render);
load();
setInterval(load, 20000);
</script>
</body>
</html>"""


def safe_md(name):
    return re.fullmatch(r"[\w\s.-]+\.md", name, flags=re.UNICODE) is not None


def safe_id(s):
    return re.fullmatch(r"[a-f0-9-]{8,40}", s) is not None


def read_json(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


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

    def authorized(self):
        if not API_KEY:
            return True
        return self.headers.get("X-API-Key", "") == API_KEY

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if not path.startswith("/api/"):
            return self.send(404, '{"error":"not found"}')
        if not self.authorized():
            return self.send(401, '{"error":"X-API-Key manquant ou invalide"}')

        if path == "/api/fiches":
            fiches = []
            if os.path.isdir(FICHES):
                for f in sorted(os.listdir(FICHES)):
                    if not f.endswith(".md"):
                        continue
                    st = os.stat(os.path.join(FICHES, f))
                    fiches.append({
                        "file": f,
                        "name": f[:-3].replace("-", " "),
                        "updated": time.strftime("%d/%m %H:%M", time.localtime(st.st_mtime)),
                        "mtime": st.st_mtime,
                    })
            fiches.sort(key=lambda x: -x["mtime"])
            archived = 0
            if os.path.isdir(ARCHIVE):
                for f in os.listdir(ARCHIVE):
                    try:
                        archived += sum(1 for _ in open(os.path.join(ARCHIVE, f)))
                    except OSError:
                        pass
            pending = len([f for f in os.listdir(PENDING)]) if os.path.isdir(PENDING) else 0
            last_sync = None
            if os.path.exists(LOG):
                for line in reversed(open(LOG, errors="replace").readlines()[-200:]):
                    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) — synchronisation", line)
                    if m:
                        last_sync = m.group(1)
                        break
            return self.send(200, json.dumps({
                "fiches": fiches, "archived": archived,
                "pending": pending, "lastSync": last_sync,
            }, ensure_ascii=False))

        if path.startswith("/api/fiche/"):
            name = path[len("/api/fiche/"):]
            full = os.path.join(FICHES, name)
            if safe_md(name) and os.path.isfile(full):
                return self.send(200, open(full, encoding="utf-8", errors="replace").read(),
                                 "text/markdown; charset=utf-8")
            return self.send(404, '{"error":"fiche introuvable"}')

        if path == "/api/contacts":
            phones = read_json(PHONES, {})
            fiches = {f[:-3] for f in os.listdir(FICHES) if f.endswith(".md")} if os.path.isdir(FICHES) else set()
            out = [{"chatId": cid, "phone": ph} for cid, ph in phones.items()]
            return self.send(200, json.dumps({"contacts": out, "fiches": sorted(fiches)}, ensure_ascii=False))

        if path.startswith("/api/messages/"):
            slug = path[len("/api/messages/"):]
            if not re.fullmatch(r"[\w-]+", slug, flags=re.UNICODE):
                return self.send(400, '{"error":"slug invalide"}')
            full = os.path.join(ARCHIVE, slug + ".jsonl")
            if not os.path.isfile(full):
                return self.send(404, '{"error":"aucun message archivé pour ce contact"}')
            limit = 200
            try:
                limit = max(1, min(5000, int(parse_qs(parsed.query).get("limit", ["200"])[0])))
            except ValueError:
                pass
            lines = open(full, encoding="utf-8", errors="replace").readlines()[-limit:]
            msgs = [json.loads(l) for l in lines if l.strip()]
            return self.send(200, json.dumps({"contact": slug, "count": len(msgs), "messages": msgs}, ensure_ascii=False))

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
                        items.append({"id": f[:-3], "question": first.lstrip("# ") or f,
                                      "status": "done",
                                      "date": time.strftime("%d/%m %H:%M", time.localtime(os.stat(p).st_mtime)),
                                      "mtime": os.stat(p).st_mtime})
            items.sort(key=lambda x: -x["mtime"])
            return self.send(200, json.dumps({"analyses": items}, ensure_ascii=False))

        if path.startswith("/api/analyses/"):
            aid = path[len("/api/analyses/"):]
            if not safe_id(aid):
                return self.send(400, '{"error":"id invalide"}')
            done = os.path.join(ANALYSES, aid + ".md")
            if os.path.isfile(done):
                return self.send(200, json.dumps({
                    "id": aid, "status": "done",
                    "result": open(done, encoding="utf-8", errors="replace").read(),
                }, ensure_ascii=False))
            if os.path.isfile(os.path.join(REQUESTS, aid + ".txt")):
                return self.send(200, json.dumps({"id": aid, "status": "pending"}))
            return self.send(404, '{"error":"analyse introuvable"}')

        return self.send(404, '{"error":"not found"}')

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path != "/api/analyses":
            return self.send(404, '{"error":"not found"}')
        if not self.authorized():
            return self.send(401, '{"error":"X-API-Key manquant ou invalide"}')
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            question = str(body.get("question", "")).strip()
        except (ValueError, TypeError):
            return self.send(400, '{"error":"corps JSON invalide"}')
        if not question or len(question) > 2000:
            return self.send(400, '{"error":"question vide ou trop longue (max 2000 caractères)"}')
        aid = uuid.uuid4().hex[:12]
        os.makedirs(REQUESTS, exist_ok=True)
        with open(os.path.join(REQUESTS, aid + ".txt"), "w", encoding="utf-8") as f:
            f.write(question)
        return self.send(202, json.dumps({
            "id": aid, "status": "pending",
            "result_url": f"/api/analyses/{aid}",
        }))


if __name__ == "__main__":
    print(f"[dashboard] http://0.0.0.0:{PORT}" + (" (API protégée par clé)" if API_KEY else ""))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
