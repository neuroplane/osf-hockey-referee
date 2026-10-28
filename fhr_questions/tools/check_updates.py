#!/usr/bin/env python3
"""Быстрая сверка базы вопросов referees.fhr.ru с локальной копией.

Режимы:
  python3 check_updates.py          — быстрая проверка questionsLastUpdate
                                      (один запрос, ничего не скачивает)
  python3 check_updates.py --diff   — скачать всю базу и показать дифф:
                                      добавленные/удалённые/изменённые вопросы
                                      и сдвиги позиций активных карточек.
  python3 check_updates.py --apply  — применить обновление полностью:
      questions.json → questions.md / questions_deleted.md → сдвиг ключей
      ANSWERS и GOLD в gen_answers.py (новым вопросам — карточки-заглушки
      TODO) → перегенерация answers.md, страниц и данных квиза/тренажёра.

После --apply остаётся: заполнить TODO-карточки, повторно запустить
gen_answers.py, задеплоить (deploy_referee.sh) и запушить.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FQ = ROOT / "fhr_questions"
TOOLS = FQ / "tools"
GEN = TOOLS / "gen_answers.py"
QJSON = FQ / "questions.json"
API = "https://referees.fhr.ru/graphql"

QUERY_META = """query { examPlatform { questionsLastUpdate
    questions(pagination: {page: 1}) { pageInfo { totalItems } } } }"""

QUERY_PAGE = """query QuestionsQuery($pagination: ExamPlatformPaginationInput!) {
    examPlatform { questionsLastUpdate questions(pagination: $pagination) {
        items { id active updatedAt numRule text
            video { id name subDir version originalName durationMs
                    videoFileGroup { moduleName entityName } }
            photo { id name subDir version originalName
                    imageFileGroup { moduleName entityName } } }
        pageInfo { page totalPages totalItems } } } }"""


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    for a in range(5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception:
            if a == 4:
                raise
            time.sleep(2 * (a + 1))


def fetch_all():
    items, lu, tp = [], None, 1
    page = 1
    while page <= tp:
        d = gql(QUERY_PAGE, {"pagination": {"page": page}})["data"]["examPlatform"]
        lu = d["questionsLastUpdate"]
        q = d["questions"]
        items += q["items"]
        tp = q["pageInfo"]["totalPages"]
        print(f"  page {page}/{tp}")
        page += 1
        if page <= tp:
            time.sleep(0.4)
    return lu, items


def short(q, n=80):
    s = re.sub(r"<[^>]+>", " ", q.get("text") or "")
    return re.sub(r"\s+", " ", s).strip()[:n]


def build_diff(old_items, new_items):
    old = {q["id"]: q for q in old_items}
    new = {q["id"]: q for q in new_items}
    added = [i for i in new if i not in old]
    removed = [i for i in old if i not in new]
    changed = [
        (i, [k for k in new[i] if new[i].get(k) != old[i].get(k)])
        for i in new if i in old and new[i] != old[i]
    ]
    old_act = [q["id"] for q in old_items if q["active"]]
    new_act = [q["id"] for q in new_items if q["active"]]
    newpos = {qid: i + 1 for i, qid in enumerate(new_act)}
    # старая позиция карточки -> новая (None = вопрос ушёл из активных)
    remap = {i + 1: newpos.get(qid) for i, qid in enumerate(old_act)}
    inserts = {p: qid for qid, p in newpos.items() if qid not in old}
    drops = {p: qid for p, qid in enumerate(old_act, 1) if qid not in newpos}
    moved = {p: np for p, np in remap.items() if np is not None and np != p}
    return dict(added=added, removed=removed, changed=changed, remap=remap,
                inserts=inserts, drops=drops, moved=moved,
                new=new, old=old, old_items=old_items, new_items=new_items)


def report(d):
    print(f"добавлено: {len(d['added'])} | удалено: {len(d['removed'])} "
          f"| изменено: {len(d['changed'])} | активных: "
          f"{sum(1 for q in d['new_items'] if q['active'])}")
    for qid in d['added']:
        q = d['new'][qid]
        media = " [video]" if q.get("video") else (" [photo]" if q.get("photo") else "")
        print(f"  + id {qid} {'актив.' if q['active'] else 'УДАЛЁН'} "
              f"§{q.get('numRule')}{media} — {short(q)}")
    for qid in d['removed']:
        q = d['old'][qid]
        print(f"  - id {qid} §{q.get('numRule')} — {short(q)}")
    for qid, fields in d['changed']:
        q = d['new'][qid]
        print(f"  ~ id {qid} поля {fields} — {short(q)}")
    if d['inserts']:
        print(f"новые активные позиции: {sorted(d['inserts'])} "
              f"(карточки со сдвигом: {[p for p in sorted(d['inserts'])]})")
    if d['drops']:
        print(f"ушли из активных: карточки {sorted(d['drops'])} "
              f"(id {[d['drops'][p] for p in sorted(d['drops'])]})")
    if d['moved']:
        rng = sorted(d['moved'])
        print(f"сдвинутых карточек: {len(rng)} "
              f"(например {rng[0]} -> {d['moved'][rng[0]]})")
    if not (d['added'] or d['removed'] or d['changed']):
        print("изменений нет — только дата lastUpdate")


def patch_gen_answers(d):
    """Ремап номеров карточек в ANSWERS/GOLD + заглушки для новых вопросов."""
    remap, inserts, new = d['remap'], d['inserts'], d['new']
    lines = GEN.read_text(encoding='utf-8').splitlines(keepends=True)
    out, kind = [], None
    dropped = []
    for ln in lines:
        m = re.match(r"^(ANSWERS\s*=\s*\{|ANSWERS\.update\(\{|GOLD\s*=\s*\{)", ln)
        if m:
            kind = 'gold' if 'GOLD' in m.group(1) else 'answers'
            out.append(ln)
            continue
        if kind and re.match(r"^\}\)?,?\s*$", ln):
            kind = None
            out.append(ln)
            continue
        if kind == 'answers':
            m = re.match(r"^(\s*)(\d+)(:.*)$", ln)
            if m:
                old_pos = int(m.group(2))
                np = remap.get(old_pos)
                if np is None:
                    dropped.append((old_pos, m.group(3).strip()))
                    continue
                out.append(f"{m.group(1)}{np}{m.group(3)}\n")
                continue
        elif kind == 'gold':
            def repl(mm):
                np = remap.get(int(mm.group(1)))
                return f"{np}," if np is not None else ""
            nl = re.sub(r"(\d+),", repl, ln)
            if nl.strip():
                out.append(nl)
            continue
        m = re.match(r"^(ANSWERS\[)(\d+)(\].*)$", ln)
        if m:
            np = remap.get(int(m.group(2)))
            if np is None:
                dropped.append((int(m.group(2)), m.group(3).strip()))
                continue
            out.append(f"{m.group(1)}{np}{m.group(3)}\n")
            continue
        out.append(ln)

    if inserts:
        entries = []
        for np in sorted(inserts):
            q = new[inserts[np]]
            ref = (q.get('numRule') or '—').split(',')[0].strip()
            entries.append(f'{np}:("TODO — {short(q, 70)}", "{ref}"),\n')
        stub = "ANSWERS.update({\n" + "".join(entries) + "})\n\n"
        for i, ln in enumerate(out):
            if re.match(r"^N = len\(ACT\)", ln):
                out.insert(i, stub)
                break
        else:
            out.append("\n" + stub)
        print(f"  вставлено TODO-карточек: {len(inserts)} "
              f"(позиции {sorted(inserts)}) — заполнить ответы!")
    if dropped:
        print(f"  карточки-сироты удалены: {[p for p, _ in dropped]}")
        for p, t in dropped:
            print(f"    #{p} {t[:90]}")
    GEN.write_text("".join(out), encoding='utf-8')


def rebuild_md(items, last_update):
    sys.path.insert(0, str(TOOLS))
    import download_questions as dq
    docs = dq.load_rules()
    act = [q for q in items if q["active"]]
    dl = [q for q in items if not q["active"]]
    dq.build_md(act, docs, FQ / "questions.md", "Вопросы для судей ФХР", last_update)
    dq.build_md(dl, docs, FQ / "questions_deleted.md",
                "Вопросы для судей ФХР — УДАЛЁННЫЕ", last_update)
    print(f"  questions.md: {len(act)} активных, {len(dl)} удалённых")


def run(*args):
    r = subprocess.run([sys.executable, *args], cwd=TOOLS)
    if r.returncode:
        raise SystemExit(f"ошибка: {args}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--diff', action='store_true', help='показать дифф без записи')
    ap.add_argument('--apply', action='store_true', help='применить обновление')
    args = ap.parse_args()

    local = json.loads(QJSON.read_text(encoding='utf-8'))

    if not (args.diff or args.apply):
        d = gql(QUERY_META)["data"]["examPlatform"]
        print(f"remote: {d['questionsLastUpdate']} · {d['questions']['pageInfo']['totalItems']}")
        print(f"local:  {local['questionsLastUpdate']} · {local['count']}")
        if d['questionsLastUpdate'] == local['questionsLastUpdate']:
            print("обновлений нет")
        else:
            print("ЕСТЬ ОБНОВЛЕНИЕ → python3 check_updates.py --diff / --apply")
        return

    print("скачиваю базу...")
    lu, items = fetch_all()
    d = build_diff(local['items'], items)
    report(d)

    if not args.apply:
        return
    if not (d['added'] or d['removed'] or d['changed']):
        print("применять нечего")
        return

    print("\nприменяю...")
    QJSON.write_text(json.dumps(
        {"questionsLastUpdate": lu, "count": len(items), "items": items},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print("  questions.json обновлён")
    rebuild_md(items, lu)
    patch_gen_answers(d)

    n_act = sum(1 for q in items if q['active'])
    print("  перегенерация страниц и данных...")
    run('gen_answers.py')
    run('build_html.py')
    run('gen_quiz.py', '../answers.md', '1', str(n_act), 'fhr-active',
        'ФХР — вопросы базы (активные)', '../../quiz/data/fhr_active.js')
    run('gen_trainer.py', '1', str(n_act), 'data_all.js')
    run('gen_trainer.py', '1', '100', 'data.js')
    if (FQ / 'manual_options.json').exists():
        run('apply_options.py')
    print("\nготово. Осталось: заполнить TODO-карточки в gen_answers.py,"
          " повторно запустить gen_answers.py, деплой, коммит, пуш.")


if __name__ == "__main__":
    main()
