#!/usr/bin/env python3
"""Собирает одностраничный fhr_questions/index.html:
список вопросов, клик по номеру правила -> текст правила в боковой панели.
Данные вшиты в HTML, работает офлайн (кроме ссылок на PDF/медиа).
"""
import json
import re
from pathlib import Path

from download_questions import (
    DOC_LABELS, DOC_ORDER, EKP_APPENDICES,
    clean_html, load_rules, media_url, numrule_refs, resolve_ref,
)

BASE = Path(__file__).parent
OUT = BASE / "fhr_questions" / "index.html"

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вопросы для судей ФХР</title>
<style>
  :root { --bg:#0f1420; --panel:#171e2e; --line:#26304a; --txt:#dfe6f3;
          --dim:#8b97b3; --acc:#4da3ff; --del:#e05555; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif;
         background:var(--bg); color:var(--txt); }
  header { position:sticky; top:0; z-index:10; background:var(--panel);
           border-bottom:1px solid var(--line); padding:10px 16px;
           display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  header h1 { font-size:16px; margin:0 12px 0 0; }
  input[type=search] { flex:1; min-width:180px; padding:7px 12px; border-radius:8px;
           border:1px solid var(--line); background:var(--bg); color:var(--txt); }
  .filters button { margin-right:6px; padding:6px 12px; border-radius:8px;
           border:1px solid var(--line); background:transparent; color:var(--dim);
           cursor:pointer; }
  .filters button.on { background:var(--acc); border-color:var(--acc); color:#04121f; font-weight:600; }
  main { display:flex; align-items:flex-start; }
  #list { flex:1; min-width:0; padding:8px 16px 60px; }
  .q { border-bottom:1px solid var(--line); padding:12px 4px; }
  .q .meta { font-size:12.5px; color:var(--dim); margin-bottom:4px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;}
  .q .txt { white-space:pre-wrap; }
  .q.deleted .txt { color:var(--dim); }
  .badge { font-size:11px; font-weight:700; color:#fff; background:var(--del);
           border-radius:4px; padding:1px 6px; }
  .rule { display:inline-block; margin:2px 6px 0 0; padding:2px 8px; font-size:12.5px;
          border:1px solid var(--acc); border-radius:12px; color:var(--acc);
          cursor:pointer; user-select:none; }
  .rule:hover { background:var(--acc); color:#04121f; }
  .rule.none { border-color:var(--line); color:var(--dim); cursor:default; }
  .rule.none:hover { background:transparent; color:var(--dim); }
  .q a.media { font-size:12.5px; color:var(--dim); margin-right:10px; }
  .backdrop { position:fixed; inset:0; background:rgba(0,0,0,.55);
              z-index:20; display:none; }
  .backdrop.open { display:block; }
  aside { position:fixed; left:50%; top:8vh; transform:translateX(-50%);
          width:min(95%,360px); max-height:84vh; overflow-y:auto;
          background:var(--panel); border:1px solid var(--line); border-radius:14px;
          box-shadow:0 18px 60px rgba(0,0,0,.6);
          padding:22px 24px; display:none; z-index:21; }
  aside.open { display:block; }
  aside h2 { font-size:15px; margin:0 0 6px; padding-right:26px; line-height:1.35; }
  aside .doc { font-size:12.5px; color:var(--dim); margin-bottom:16px; }
  aside .doc a { color:var(--acc); }
  aside .body { font-size:12.5px; line-height:1.7; }
  aside .body p { margin:0 0 12px; }
  aside .body p:last-child { margin-bottom:0; }
  aside .body p.h { font-weight:700; color:var(--acc); margin-top:16px; }
  aside .close { position:absolute; top:10px; right:12px; cursor:pointer;
          color:var(--dim); font-size:20px; border:none; background:none; }
</style>
</head>
<body>
<header>
  <h1>Вопросы судей ФХР</h1>
  <input type="search" id="search" placeholder="Поиск по тексту / номеру правила…">
  <span class="filters">
    <button data-f="all" class="on">Все</button>
    <button data-f="active">Активные</button>
    <button data-f="deleted">Удалённые</button>
  </span>
  <span id="count" style="color:var(--dim);font-size:12.5px"></span>
</header>
<main>
  <div id="list"></div>
  <div class="backdrop" id="backdrop" onclick="closePanel()"></div>
  <aside id="panel">
    <button class="close" onclick="closePanel()">&times;</button>
    <h2 id="p-title"></h2>
    <div class="doc" id="p-doc"></div>
    <div class="body" id="p-body"></div>
  </aside>
</main>
<script>
const QUESTIONS = __QUESTIONS__;
const RULES = __RULES__;
let filter = "all", query = "";

function esc(s){ const d=document.createElement("div"); d.textContent=s||""; return d.innerHTML; }

function render(){
  const list = document.getElementById("list");
  const q = query.toLowerCase();
  let html = "", n = 0;
  for (const it of QUESTIONS){
    if (filter==="active" && !it.active) continue;
    if (filter==="deleted" && it.active) continue;
    if (q && !(it.text.toLowerCase().includes(q) || (it.numRule||"").toLowerCase().includes(q))) continue;
    n++;
    html += `<div class="q ${it.active?"":"deleted"}"><div class="meta">`
      + `<span>#${n} · id ${it.id}</span>`
      + (it.numRule ? `<span>правило: ${esc(it.numRule)}</span>` : "")
      + (it.active ? "" : `<span class="badge">УДАЛЁН</span>`)
      + `</div><div class="txt">${esc(it.text)}</div><div>`;
    for (const m of it.media) html += `<a class="media" href="${m.u}" target="_blank">${m.k}</a>`;
    for (const r of it.refs){
      if (r.key) html += `<span class="rule" onclick="showRule('${r.doc}','${r.key}')">§ ${esc(r.num)}</span>`;
      else html += `<span class="rule none">${esc(r.num)}</span>`;
    }
    html += "</div></div>";
  }
  list.innerHTML = html;
  document.getElementById("count").textContent = n + " вопросов";
}

// Разбивает текст из PDF на абзацы: переносы внутри абзаца склеиваются,
// новый абзац — после короткой строки с концом предложения, на строках-заголовках
// капсом и на элементах списка (i. ii. 1. – и т.п.)
function fmtBody(text){
  const lines = (text || "").split("\\n").map(s => s.trim()).filter(Boolean);
  if (!lines.length) return "<p>—</p>";
  const lens = lines.map(l => l.length).sort((a,b) => a-b);
  const med = lens[Math.floor(lens.length/2)] || 60;
  const caps = s => { const L = s.replace(/[^A-Za-zА-Яа-яЁё]/g,""); return L.length >= 4 && L === L.toUpperCase(); };
  const item = s => /^([ivxl]{1,4}\\.|\\d{1,2}[.)]|[–—-]|п\\.|[а-я]\\))\\s/i.test(s);
  let html = "", buf = [], cls = "";
  const flush = () => {
    if (!buf.length) return;
    html += "<p" + (cls ? ' class="' + cls + '"' : "") + ">" + esc(buf.join(" ")) + "</p>";
    buf = []; cls = "";
  };
  for (const ln of lines){
    const isCaps = caps(ln), isItem = item(ln);
    if (buf.length && (isCaps || isItem)) flush();
    buf.push(ln);
    if (isCaps) cls = "h";
    const endTerm = /[.!?:;»"]$/.test(ln);
    if (isCaps || (endTerm && ln.length < med * 0.82)) flush();
  }
  flush();
  return html;
}

function showRule(doc, key){
  const r = RULES[doc + "|" + key]; if(!r) return;
  document.getElementById("p-title").textContent = "Правило " + key + " — " + r.title;
  document.getElementById("p-doc").innerHTML =
    esc(r.docLabel) + ", стр. " + r.page + ' — <a href="' + r.url + '#page=' + r.page + '" target="_blank">открыть PDF</a>';
  document.getElementById("p-body").innerHTML = fmtBody(r.text);
  document.getElementById("panel").classList.add("open");
  document.getElementById("backdrop").classList.add("open");
}
function closePanel(){
  document.getElementById("panel").classList.remove("open");
  document.getElementById("backdrop").classList.remove("open");
}

document.getElementById("search").addEventListener("input", e=>{ query=e.target.value; render(); });
document.querySelectorAll(".filters button").forEach(b=>b.addEventListener("click", ()=>{
  document.querySelectorAll(".filters button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); filter=b.dataset.f; render();
}));
document.addEventListener("keydown", e=>{ if(e.key==="Escape") closePanel(); });
render();
</script>
</body>
</html>
"""


def main() -> None:
    data = json.loads((BASE / "fhr_questions" / "questions.json").read_text(encoding="utf-8"))
    docs = load_rules()

    questions, used = [], {}
    for q in data["items"]:
        refs = []
        # приложения ("Прил. N")
        for m in re.findall(r"Прил\.?\s*(\d)", q.get("numRule") or ""):
            n = int(m)
            if "ekp_2022_23" in docs and n in EKP_APPENDICES:
                pg, name = EKP_APPENDICES[n]
                key = f"appendix-{n}"
                used[("ekp_2022_23", key)] = {
                    "title": f"Приложение {n} — {name}", "page": pg,
                    "text": "Смотрите PDF — приложение содержит графику/таблицы.",
                    "url": docs["ekp_2022_23"]["url"],
                    "docLabel": DOC_LABELS["ekp_2022_23"],
                }
                refs.append({"num": f"Прил. {n}", "doc": "ekp_2022_23", "key": key})
        for ref in numrule_refs(q.get("numRule")):
            res = resolve_ref(ref, docs)
            if res:
                doc_key, key = res
                rule = docs[doc_key]["rules"][key]
                text = rule["text"]
                if not text.strip() and "НЕ ПРИМЕНЯЕТСЯ" in rule["title"].upper():
                    # правило не применяется в ЕКП — показываем аналог из ИИХФ,
                    # только если заголовки совпадают (нумерация расходится)
                    text = "Правило не применяется в соревнованиях ЕКП."

                    def words(t: str) -> set[str]:
                        t = re.sub(r"НЕ\s*ПРИМЕНЯЕТСЯ", "", t.upper())
                        return set(re.findall(r"[А-ЯЁA-Z]{3,}", t))

                    alt = None
                    if doc_key != "iihf_2026_27":
                        cand = docs.get("iihf_2026_27", {}).get("rules", {}).get(key)
                        if cand and cand["text"].strip():
                            w = words(rule["title"])
                            if w and len(w & words(cand["title"])) / len(w) >= 0.6:
                                alt = cand
                    if alt:
                        text += (
                            f"\n\nАналог — книга ИИХФ 2026/27, правило {key} (стр. {alt['page']}):\n"
                            f"{alt['title']}\n{alt['text']}"
                        )
                if not text.strip() and "." not in key:
                    # заголовок правила без текста — собираем все подпункты N.*
                    parts = [
                        (k, v) for k, v in docs[doc_key]["rules"].items()
                        if k.startswith(key + ".")
                    ]
                    parts.sort(key=lambda kv: int(kv[0].split(".")[1]))
                    text = "\n\n".join(
                        f"{k}. {v['title']}\n{v['text']}".strip() for k, v in parts
                    )
                used[(doc_key, key)] = {
                    "title": rule["title"], "page": rule["page"],
                    "text": text, "url": docs[doc_key]["url"],
                    "docLabel": DOC_LABELS[doc_key],
                }
                refs.append({"num": ref, "doc": doc_key, "key": key})
            else:
                refs.append({"num": ref, "doc": None, "key": None})
        media = []
        for kind, key in (("images", "photo"), ("videos", "video")):
            url = media_url(kind, q.get(key))
            if url:
                media.append({"k": key, "u": url})
        questions.append({
            "id": q["id"], "active": q["active"],
            "numRule": q.get("numRule") or "", "text": clean_html(q.get("text", "")),
            "refs": refs, "media": media,
        })

    rules = {f"{d}|{k}": v for (d, k), v in used.items()}

    def js(obj) -> str:
        return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")

    html = HTML.replace("__QUESTIONS__", js(questions)).replace("__RULES__", js(rules))
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT} — {len(questions)} questions, {len(rules)} rules, {OUT.stat().st_size//1024} KB")


if __name__ == "__main__":
    main()
