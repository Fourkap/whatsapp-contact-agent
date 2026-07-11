#!/usr/bin/env python3
"""Mini dashboard web pour consulter les fiches contacts WhatsApp.
Servi par le conteneur sur le port 2786 — aucune dépendance externe."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

DATA = os.environ.get("DATA_DIR", "/data")
FICHES = os.path.join(DATA, "fiches")
PENDING = os.path.join(DATA, "_state", "pending")
ARCHIVE = os.path.join(DATA, "_state", "archive")
LOG = os.path.join(DATA, "_state", "logs", "agent.log")
PORT = int(os.environ.get("DASHBOARD_PORT", "2786"))

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fiches WhatsApp</title>
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
  header { padding: 14px 22px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 17px; font-weight: 650; }
  header .stats { color: var(--muted); font-size: 13px; }
  header .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); margin-right: 5px; }
  main { flex: 1; display: flex; min-height: 0; }
  nav { width: 290px; min-width: 220px; border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; }
  nav input { margin: 12px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-size: 14px; outline: none; }
  nav input:focus { border-color: var(--accent); }
  #list { overflow-y: auto; flex: 1; }
  .item { padding: 11px 16px; cursor: pointer; border-bottom: 1px solid var(--border); }
  .item:hover { background: var(--hover); }
  .item.active { background: var(--accent-soft); border-left: 3px solid var(--accent); padding-left: 13px; }
  .item .n { font-weight: 600; font-size: 14px; }
  .item .d { color: var(--muted); font-size: 12px; margin-top: 2px; }
  #view { flex: 1; overflow-y: auto; padding: 30px 40px; max-width: 860px; }
  #view h1 { font-size: 24px; margin-bottom: 14px; }
  #view h2 { font-size: 16px; margin: 22px 0 8px; color: var(--accent); }
  #view ul { padding-left: 22px; margin: 6px 0; }
  #view li { margin: 3px 0; }
  #view p { margin: 7px 0; }
  #view strong { font-weight: 650; }
  #view em { color: var(--muted); }
  .empty { color: var(--muted); text-align: center; margin-top: 80px; }
  .badge { font-size: 11px; background: var(--accent-soft); color: var(--accent); padding: 2px 8px; border-radius: 10px; }
  @media (max-width: 700px) { nav { width: 160px; } #view { padding: 18px; } }
</style>
</head>
<body>
<header>
  <h1>📇 Fiches WhatsApp</h1>
  <span class="stats" id="stats"><span class="dot"></span>chargement…</span>
</header>
<main>
  <nav>
    <input id="search" type="search" placeholder="Rechercher un contact…">
    <div id="list"></div>
  </nav>
  <div id="view"><div class="empty">Sélectionne un contact à gauche</div></div>
</main>
<script>
let fiches = [], current = null;

function md(src) {
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  let out = [], inList = false;
  for (let line of src.split('\\n')) {
    let h = esc(line)
      .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
      .replace(/(^|\\s)\\*(?!\\s)(.+?)\\*(?=\\s|$|[.,;:])/g, '$1<em>$2</em>');
    if (/^# /.test(h)) { if(inList){out.push('</ul>');inList=false;} out.push('<h1>'+h.slice(2)+'</h1>'); }
    else if (/^## /.test(h)) { if(inList){out.push('</ul>');inList=false;} out.push('<h2>'+h.slice(3)+'</h2>'); }
    else if (/^- /.test(h)) { if(!inList){out.push('<ul>');inList=true;} out.push('<li>'+h.slice(2)+'</li>'); }
    else if (h.trim()==='') { if(inList){out.push('</ul>');inList=false;} }
    else { if(inList){out.push('</ul>');inList=false;} out.push('<p>'+h+'</p>'); }
  }
  if (inList) out.push('</ul>');
  return out.join('');
}

async function load() {
  const r = await fetch('/api/fiches'); const data = await r.json();
  fiches = data.fiches;
  document.getElementById('stats').innerHTML =
    '<span class="dot"></span>' + data.fiches.length + ' fiche(s) · ' +
    data.archived + ' message(s) traités · ' + (data.pending ? data.pending + ' en attente · ' : '') +
    'dernière synchro : ' + (data.lastSync || '—');
  render();
}

function render() {
  const q = document.getElementById('search').value.toLowerCase();
  const el = document.getElementById('list');
  el.innerHTML = '';
  fiches.filter(f => f.name.toLowerCase().includes(q)).forEach(f => {
    const d = document.createElement('div');
    d.className = 'item' + (current === f.file ? ' active' : '');
    d.innerHTML = '<div class="n">' + f.name + '</div><div class="d">maj ' + f.updated + '</div>';
    d.onclick = () => open(f.file);
    el.appendChild(d);
  });
  if (!el.children.length) el.innerHTML = '<div class="empty" style="margin-top:30px">Aucune fiche</div>';
}

async function open(file) {
  current = file; render();
  const r = await fetch('/api/fiche/' + encodeURIComponent(file));
  document.getElementById('view').innerHTML = md(await r.text());
}

document.getElementById('search').addEventListener('input', render);
load();
setInterval(load, 60000);
</script>
</body>
</html>"""


def safe_name(name):
    return re.fullmatch(r"[\w\s.-]+\.md", name, flags=re.UNICODE) is not None


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

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        if path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
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
                        "updated": __import__("time").strftime("%d/%m %H:%M", __import__("time").localtime(st.st_mtime)),
                        "mtime": st.st_mtime,
                    })
            fiches.sort(key=lambda x: -x["mtime"])
            archived = pending = 0
            if os.path.isdir(ARCHIVE):
                for f in os.listdir(ARCHIVE):
                    try:
                        archived += sum(1 for _ in open(os.path.join(ARCHIVE, f)))
                    except OSError:
                        pass
            if os.path.isdir(PENDING):
                pending = len([f for f in os.listdir(PENDING) if f.endswith(".jsonl")])
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
            if safe_name(name) and os.path.isfile(full):
                return self.send(200, open(full, encoding="utf-8", errors="replace").read(),
                                 "text/plain; charset=utf-8")
            return self.send(404, '{"error":"fiche introuvable"}')
        return self.send(404, '{"error":"not found"}')


if __name__ == "__main__":
    print(f"[dashboard] http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
