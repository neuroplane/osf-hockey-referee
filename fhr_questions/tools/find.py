#!/usr/bin/env python3
# Быстрый поиск ответа по базе ФХР во время зачёта.
# Вызов:  python3 fhr_questions/tools/find.py "фраза из вопроса"
#         python3 fhr_questions/tools/find.py 5cb64397847b76119d5f229a7527bddb   (имя медиафайла)
#         python3 fhr_questions/tools/find.py --id 1328                          (по id ФХР)
# Печатает: № карточки, ★/—, id, вопрос, ответ, правило + текст правила ЕКП.
import ast, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG = re.compile(r'<[^>]+>')
WS = re.compile(r'\s+')

def clean(t):
    return WS.sub(' ', TAG.sub(' ', t or '')).strip()

def norm(t):
    t = clean(t).lower().replace('ё', 'е')
    return re.sub(r'[^a-zа-я0-9]+', ' ', t).strip()

# --- источник ответов: ANSWERS и GOLD из gen_answers.py, без его запуска ---
src = (ROOT / 'tools/gen_answers.py').read_text(encoding='utf-8')
GOLD = ast.literal_eval(re.search(r'GOLD\s*=\s*(\{.*?\n\})', src, re.S).group(1))
ANSWERS = {}
for m in re.finditer(
        r'ANSWERS\s*=\s*(\{.*?\n\})|ANSWERS\.update\(\s*(\{.*?\n\})\s*\)|ANSWERS\[(\d+)\]\s*=\s*(\([^\n]*\))',
        src, re.S):
    if m.group(1):
        ANSWERS.update(ast.literal_eval(m.group(1)))
    elif m.group(2):
        ANSWERS.update(ast.literal_eval(m.group(2)))
    else:
        ANSWERS[int(m.group(3))] = ast.literal_eval(m.group(4))
AEDITS = json.load(open(ROOT / 'aedits.json', encoding='utf-8')) if (ROOT / 'aedits.json').exists() else {}

Q = json.load(open(ROOT / 'questions.json', encoding='utf-8'))
ACT = [q for q in Q['items'] if q['active']]
ALL = Q['items']

RULES = json.load(open(ROOT / 'rules/rules_ekp_2022_23.json', encoding='utf-8'))['rules']

def card_of(q):
    # № карточки = позиция в ACT
    try:
        return ACT.index(q) + 1
    except ValueError:
        return None

def answer_of(i, qid):
    ov = AEDITS.get(str(qid))
    ans, rule = ANSWERS.get(i, ('— нет карточки —', ''))
    if ov:
        ans = ov.get('ans') or ans
        rule = ov.get('rule') or rule
    gold = i in GOLD or bool(ov and ov.get('gold'))
    return ans, rule, gold

def rule_text(rule):
    out = []
    for r in re.findall(r'§(\d+[\.\d]*)', rule or ''):
        rec = RULES.get(r)
        if rec:
            out.append(f"--- §{r} {rec.get('title','')}\n{rec.get('text','')[:1800]}")
    return '\n'.join(out)

def show(q, score=None):
    i = card_of(q)
    ans, rule, gold = answer_of(i, q['id']) if i else answer_of(0, q['id'])
    head = f"[{i or 'удалён'}]{' ★' if gold else ''} id {q['id']} · §{q.get('numRule','')}"
    if score is not None:
        head += f" · score {score:.2f}"
    print('=' * 16, head, '=' * 16)
    print(clean(q['text'])[:400])
    print(f"\n✅ {ans}   _{rule}_")
    if q.get('photo'): print(f"🖼 {q['photo']}")
    if q.get('video'): print(f"🎥 {q['video']}")
    rt = rule_text(rule)
    if rt:
        print(f"\n{rt}")
    print()

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    if args[0] == '--id':
        for q in ALL:
            if q['id'] == int(args[1]):
                show(q); return
        print('id не найден'); return

    query = ' '.join(args)

    # 1) медиафайл по имени
    if re.fullmatch(r'[0-9a-f]{20,}(\.\w+)?', query.split('/')[-1]):
        name = query.split('/')[-1].split('?')[0]
        for q in ALL:
            if name in str(q.get('photo') or '') or name in str(q.get('video') or ''):
                show(q); return
        print('медиа не найдено, ищу по тексту...')

    # 2) текстовый поиск по активным (резервно — по всем)
    nq = norm(query)
    qtok = set(nq.split())
    def score(q):
        n = norm(q['text'])
        if n and n in nq: return 1.0
        if nq in n: return 1.0
        t = set(n.split())
        return len(qtok & t) / max(1, len(t))
    for pool, label in ((ACT, 'активные'), (ALL, 'вся база')):
        hits = sorted(pool, key=score, reverse=True)[:3]
        hits = [h for h in hits if score(h) >= 0.45]
        if hits:
            if label == 'вся база': print('(в активных не нашлось — поиск по всей базе)')
            for h in hits:
                show(h, score(h))
            return
    print('ничего не найдено')

main()
