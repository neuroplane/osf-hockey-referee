#!/usr/bin/env python3
# Генератор данных тренажёра: из answers_1_1276.md берёт вопросы N..M,
# к каждому собирает 4 варианта ответа (правильный + 3 дистрактора
# из ответов на вопросы того же правила, иначе — из общего пула).
# Использование: python3 gen_trainer.py [from] [to]
import json, re, random, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

FR, TO = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1, 100)
OUT = ROOT / 'trainer' / (sys.argv[3] if len(sys.argv) > 3 else 'data.js')
random.seed(42)

answers_md = max(ROOT.glob('answers_1_*.md'),
                 key=lambda p: int(re.search(r'_(\d+)\.md$', p.name).group(1)))
txt = answers_md.read_text()
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
        for u in re.findall(r'\[(видео|фото)\]\((https?://[^)]+)\)', ln):
            media.append(u)
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

items = {}
pool_by_rule = {}
pool_all = []
for p in parts:
    num, q, body, ans, rule, media = parse(p)
    if not ans:
        continue
    items[num] = dict(q=q, body=body, ans=ans, rule=rule, media=media)
    key = re.search(r'(\d+)', rule)
    key = key.group(1) if key else ''
    pool_by_rule.setdefault(key, set()).add(ans)
    pool_all.append(ans)

YESNO = [
    'Да',
    'Нет',
    'Только с разрешения Главного судьи',
    'Только во время остановки игры',
    'Разрешается, если команда предупреждена заранее',
    'Запрещено — наказывается Малым штрафом',
]

def is_yesno(a):
    return len(a) < 60 and re.match(r'^(Да|Нет)\b', a)

qs = []
for num in range(FR, TO + 1):
    if num not in items:
        continue
    if items[num]['ans'].startswith('Смотреть'):
        continue  # медиа-вопросы без текстового ответа — для тренажёра бесполезны
    q, body, ans, rule, media = (items[num][k] for k in ('q', 'body', 'ans', 'rule', 'media'))
    if is_yesno(ans):
        ds = [d for d in YESNO if d != ans and not d.startswith(ans.split(' ')[0])]
        ds += [d for d in YESNO if d != ans and d not in ds]
        distract = ds[:3]
    else:
        key = re.search(r'(\d+)', rule)
        same = [a for a in pool_by_rule.get(key.group(1) if key else '', set()) if a != ans]
        random.shuffle(same)
        distract = same[:3]
        if len(distract) < 3:
            extra = [a for a in pool_all if a != ans and a not in distract and len(a) < 140]
            random.shuffle(extra)
            distract += extra[:3 - len(distract)]
    opts = distract[:3] + [ans]
    random.shuffle(opts)
    qs.append(dict(
        n=num, q=q, table=table_html(body),
        media=[{'kind': k, 'url': u} for k, u in media],
        opts=opts, correct=opts.index(ans), rule=rule))

OUT.parent.mkdir(exist_ok=True)
OUT.write_text('window.QUESTIONS=' + json.dumps(qs, ensure_ascii=False) + ';\n')
print(f'{OUT}: {len(qs)} вопросов')
