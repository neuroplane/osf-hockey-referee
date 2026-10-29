#!/usr/bin/env python3
# Генератор набора вопросов для тренажёра/игры дня.
# Источник — любой markdown вида:
#   ## N. Текст вопроса
#   **Ответ:** правильный ответ
#   _§правило_
# Использование:
#   python3 gen_quiz.py <src.md> <from> <to> <id> <title> <out.js>
# Пример:
#   python3 gen_quiz.py ../answers.md 1 1276 fhr-active "ФХР — активные вопросы" ../quiz/data/fhr_active.js
import json, re, random, pathlib, sys

SRC, FR, TO, QID, TITLE, OUT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
DAILY = len(sys.argv) < 8 or sys.argv[7] != "0"
random.seed(42)

txt = pathlib.Path(SRC).read_text()
parts = re.split(r'\n## ', txt)[1:]

def parse(p):
    num = int(re.match(r'\d+', p).group(0))
    lines = p.split('\n')
    q = lines[0].replace(' ⭐', '').split(' ', 1)[1].strip()
    body, ans, rule, media = [], None, '', []
    for ln in lines[1:]:
        m = re.match(r'\*\*Ответ:\*\*\s*(.+)', ln)
        if m:
            ans = m.group(1).strip()
            continue
        if re.match(r'^_.+_\s*$', ln):
            rule = ln.strip().strip('_')
            continue
        for k, u in re.findall(r'\[(видео|фото)\]\((https?://[^)]+)\)', ln):
            media.append({'kind': k, 'url': u})
        if ln.strip().startswith('|'):
            body.append(ln.strip())
    return num, q, body, ans, rule, media

def table_html(body):
    rows = [r for r in body if r.startswith('|') and '---' not in r]
    if not rows:
        return ''
    cells = [[c.strip() for c in r.strip('|').split('|')] for r in rows]
    h = '<table class="pt">'
    for i, row in enumerate(cells):
        tag = 'th' if i == 0 else 'td'
        h += '<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in row) + '</tr>'
    return h + '</table>'

items, pool_by_rule, pool_all = {}, {}, []
for p in parts:
    num, q, body, ans, rule, media = parse(p)
    if not ans or ans in ('—',) or ans.startswith('—') or ans.startswith('Смотреть'):
        continue
    items[num] = dict(q=q, body=body, ans=ans, rule=rule, media=media)
    key = re.search(r'(\d+)', rule)
    key = key.group(1) if key else ''
    pool_by_rule.setdefault(key, set()).add(ans)
    pool_all.append(ans)

YESNO = [
    'Да', 'Нет', 'Только с разрешения Главного судьи',
    'Только во время остановки игры',
    'Разрешается, если команда предупреждена заранее',
    'Запрещено — наказывается Малым штрафом',
]

PENALTIES = [
    'Без штрафа — устное предупреждение',
    'Малый скамеечный штраф',
    'Малый штраф',
    'Малый двойной штраф (2+2)',
    'Большой штраф (5 минут)',
    'Большой штраф + Дисциплинарный до конца матча',
    'Дисциплинарный штраф (10 минут)',
    'Дисциплинарный до конца матча штраф',
    'Матч-штраф',
    'Штрафной бросок',
]

GOALS = [
    'Взятие ворот засчитывается',
    'Взятие ворот не засчитывается',
    'Взятие ворот засчитывается + штраф виновному',
    'Взятие ворот не засчитывается + штраф виновному',
    'Назначается штрафной бросок',
]

def is_yesno(a):
    return len(a) < 60 and re.match(r'^(Да|Нет)\b', a)

def norm(a):
    return re.sub(r'[^а-яa-z0-9 ]', '', a.lower()).replace('  ', ' ').strip()

def dup(a, b):
    na, nb = norm(a), norm(b)
    return na == nb or (len(na) > 8 and len(nb) > 8 and (na in nb or nb in na))

def category(a):
    if re.search(r'штраф|бросок|предупрежден', a, re.I) and len(a) < 110:
        return 'penalty'
    if re.search(r'взятие ворот|гол', a, re.I) and len(a) < 90:
        return 'goal'
    return 'other'

def pick_distractors(ans, same_rule, all_pool):
    cat = category(ans)
    canon = {'penalty': PENALTIES, 'goal': GOALS}.get(cat, [])
    out = []

    def take(src):
        for a in src:
            if len(out) >= 3:
                break
            if not dup(a, ans) and all(not dup(a, o) for o in out):
                out.append(a)

    take([a for a in same_rule if category(a) == cat])
    take(canon)
    take([a for a in same_rule])
    take([a for a in all_pool if len(a) < 140])
    return out[:3]

qs = []
for num in range(FR, TO + 1):
    if num not in items:
        continue
    it = items[num]
    q, body, ans, rule, media = it['q'], it['body'], it['ans'], it['rule'], it['media']
    if is_yesno(ans):
        ds = [d for d in YESNO if d != ans and not d.startswith(ans.split(' ')[0])]
        ds += [d for d in YESNO if d != ans and d not in ds]
        distract = ds[:3]
    else:
        key = re.search(r'(\d+)', rule)
        same = [a for a in pool_by_rule.get(key.group(1) if key else '', set()) if a != ans]
        random.shuffle(same)
        pool = pool_all[:]
        random.shuffle(pool)
        distract = pick_distractors(ans, same, pool)
    opts = distract[:3] + [ans]
    random.shuffle(opts)
    qs.append(dict(
        n=num, q=q, table=table_html(body), media=media,
        opts=opts, correct=opts.index(ans), rule=rule))

out = pathlib.Path(OUT)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    'window.QUIZ_SETS=window.QUIZ_SETS||[];window.QUIZ_SETS.push('
    + json.dumps(dict(id=QID, title=TITLE, daily=DAILY, questions=qs), ensure_ascii=False)
    + ');\n')
print(f'{out}: {len(qs)} вопросов ({QID})')
