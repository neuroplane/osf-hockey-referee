#!/usr/bin/env python3
"""Парсит книги правил/руководства (PDF) в JSON-индексы:
{ "24.2": {"title": ..., "page": ...}, "24": {...}, ... }
Формат заголовков: "ПРАВИЛО 20 БОЛЬШОЙ ШТРАФ" / "ПРАВИЛО 112 – СОВПАДАЮЩИЕ ШТРАФЫ"
и подпункты "20.1 БОЛЬШОЙ ШТРАФ" / "24.2. ПРОЦЕДУРА".
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

BASE = Path(__file__).parent / "rules_docs"

DOCS = {
    "ekp_2022_23": {
        "pdf": BASE / "ekp_referee_guide_2022_23.pdf",
        "url": "https://fhr.ru/upload/iblock/52a/Rukovodstvo-po-sudei_stvu-sorevnovanii_-EKP-2022_23.pdf",
        "header_re": r"^РУКОВОДСТВО ПО СУДЕЙСТВУ",
    },
    "iihf_2026_27": {
        "pdf": BASE / "iihf_rulebook_2026_2027.pdf",
        "url": "https://vks.fhr.ru/upload/iblock/799/79909f302d2af1ec08a5b8c3fd871081.pdf",
        "header_re": r"^ОФИЦИАЛЬНАЯ КНИГА ПРАВИЛ ИИХФ",
    },
    "iihf_2018_22": {
        "pdf": BASE / "iihf_rulebook_2018_2022.pdf",
        "url": "https://fhr.ru/upload/iblock/596/Pravila-IIKHF-2018_2022.pdf",
        "header_re": r"^(ОФИЦИАЛЬНАЯ КНИГА ПРАВИЛ|ПРАВИЛА ИИХФ)",
    },
}

RE_RULE = re.compile(r"^ПРАВИЛО\s+(\d{1,3})\s*[.–—-]?\s*(.*)$")
RE_SUB = re.compile(r"^(\d{1,3})\.(\d{1,2})\.?\s+(.+)$")
RE_PAGE_NUM = re.compile(r"^\d{1,3}\s*$")
RE_TOC_DOTS = re.compile(r"\.{4,}|\s\d{1,3}\s*$")
# разрядка букв на страницах-разделителях: «Р АЗ Д Е Л», «АЗ Д»
RE_SPACED = re.compile(r"^(?:[А-ЯA-ZЁ]{1,2}\s+)+[А-ЯA-ZЁ]{1,2}$")
RE_BULLET = re.compile(r"^[·•*]+$")
# колонтитулы: «РАЗДЕЛ 08», «04 РАЗДЕЛ · СУДЬИ», «СОДЕРЖАНИЕ», «ПРИЛОЖЕНИЕ · X»
RE_SECTION_HDR = re.compile(r"^(?:\d{1,2}\s*)?РАЗДЕЛ\b|^СОДЕРЖАНИЕ$|^ПРИЛОЖЕНИЕ\s*·")

# короткие слова, которые законно стоят отдельно — их не склеиваем
SHORT_WORDS = {
    "а", "б", "в", "и", "к", "о", "с", "у", "я",
    "бы", "же", "ли", "вы", "мы", "он", "то", "но", "ни", "не", "на", "по",
    "до", "за", "от", "об", "со", "из", "их", "ей", "ём", "ем", "им", "уж",
    "ёж", "во", "лёд", "луч", "час", "раз", "два", "три", "нет", "все", "его", "она",
    "оно", "это", "тот", "тех", "под", "над", "при", "без", "про", "для", "или",
    "как", "что", "они", "вам", "нам", "где", "мяу", "тип", "вид", "ход", "бок",
    "бой", "дно", "сок", "сон", "мел", "айс", "айс.",
}
RE_ORPHAN = re.compile(r"([А-Яа-яЁёA-Za-z]{4,})\s+([А-Яа-яЁё]{1,4})(?![А-Яа-яЁёA-Za-z])")
RE_TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z]+")


try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except ImportError:
    _MORPH = None

try:
    from wordfreq import word_frequency as wf
except ImportError:
    def wf(_w: str, _lang: str = "ru") -> float:
        return 0.0

_dict_cache: dict[str, bool] = {}


def dict_known(word: str) -> bool:
    w = word.lower()
    if w not in _dict_cache:
        _dict_cache[w] = _MORPH is not None and _MORPH.word_is_known(w)
    return _dict_cache[w]


def fix_orphans(s: str) -> str:
    """Чинит пробелы/дефисы, разорванные PDF-экстракцией:
    'СДЕРЖИВАЮЩИ Е' -> 'СДЕРЖИВАЮЩИЕ', 'разрешае тся' -> 'разрешается',
    'Матч -штрафов' -> 'Матч-штрафов', 'каких -\\nлибо' -> 'каких-либо'.
    Пару, где обе части — самостоятельные слова («только суд»), не трогаем:
    склейка исказила бы дословный текст."""
    # дефис, разорванный пробелами/переносом строки (вкл. «2-й», «25 -30»)
    s = re.sub(r"(?<=[А-Яа-яЁёA-Za-z0-9])\s+-\s*(?=[А-Яа-яЁёA-Za-z0-9])", "-", s)
    s = re.sub(r"(?<=[А-Яа-яЁёA-Za-z0-9])-\s+(?=[А-Яа-яЁёA-Za-z])", "-", s)
    # оставшийся « - » между словами — это тире, а не дефис
    s = re.sub(r"(?<=\s)-(?=\s)", "–", s)
    # слипшееся «ит.д./ит.п.» -> «и т.д./и т.п.»
    s = re.sub(r"\bи(?=т\.\s?[дпке]\.)", "и ", s)
    # «плей офф» -> «плей-офф»
    s = re.sub(r"(?i)плей\s+офф", "плей-офф", s)

    def join(m: re.Match) -> str:
        left, frag = m.group(1), m.group(2)
        if frag.lower() in SHORT_WORDS:
            return m.group(0)
        # капс-аббревиатура («ИИХФ», «ДКМ») — не обрывок слова
        if len(frag) >= 2 and frag.isupper():
            return m.group(0)
        # фрагмент — самостоятельное слово, а склейка слова не даёт:
        # скорее всего легитимная пара («только суд») — не трогаем
        if dict_known(frag) and not dict_known(left + frag):
            return m.group(0)
        return left + frag

    s = RE_ORPHAN.sub(join, s)
    # разрезание слитного — после склейки обрывков
    s = re.sub(r"(?<=[а-яёa-z])(ИИХФ|ИКХФ|ФХР|ВХЛ|КХЛ|МХЛ|НХЛ)\b", r" \1", s)
    s = re.sub(r"(?<=[а-яё])(?=(?:плей|Плей|ПЛЕЙ)-(?:офф|Офф|ОФФ)|сэйв|Сэйв)", " ", s)

    def video(m: re.Match) -> str:
        return m.group(1) + m.group(2).lower()
    return re.sub(r"(?i)\b(видео)\s+(просмотр\w*)", video, s)


def merge_spaced(text: str) -> str:
    """Склеивает разрядки — слова, разорванные PDF-экстракцией на
    короткие фрагменты: 'В ИДЕ О ПРО СМОТР' -> 'ВИДЕОПРОСМОТР',
    'ЗОН А' -> 'ЗОНА', 'МАЛ ОГО' -> 'МАЛОГО'. Группа = подряд идущие
    токены не длиннее 5 букв. Группа не трогается, если каждый её
    токен — словарное слово («во время», «не было», «то же»)."""
    tokens = list(RE_TOKEN.finditer(text))
    out: list[str] = []
    last = 0
    i = 0
    while i < len(tokens):
        m = tokens[i]
        # группа коротких токенов, разделённых пробельными символами
        j = i
        if len(m.group(0)) <= 5:
            while (
                j + 1 < len(tokens)
                and len(tokens[j + 1].group(0)) <= 5
                and text[tokens[j].end():tokens[j + 1].start()].strip() == ""
            ):
                j += 1
        group = tokens[i:j + 1]
        words = [g.group(0) for g in group]
        emit = True
        if len(words) > 1:
            # частотная проверка: частая фраза с пробелами («во время»,
            # «то же») — легитимна; редкая («зон а») — кандидат на склейку,
            # если слитный вариант — словарное слово
            def better(lo: int, hi: int) -> bool:
                phrase = " ".join(w.lower() for w in words[lo:hi])
                if wf(phrase, "ru") >= 1e-4:
                    return False
                return dict_known("".join(words[lo:hi]))

            if better(0, len(words)):
                out.append(text[last:m.start()])
                out.append("".join(words))
                last = group[-1].end()
                emit = False
            else:
                # жадно: самый длинный префикс с выигрышем по частоте
                solved: list[str] = []
                start = 0
                while start < len(words):
                    end = start
                    for k in range(len(words), start, -1):
                        if k - start > 1 and better(start, k):
                            end = k
                            break
                    if end > start:
                        solved.append("".join(words[start:end]))
                        start = end
                    else:
                        solved.append(words[start])
                        start += 1
                if len(solved) < len(words):
                    out.append(text[last:m.start()])
                    out.append(" ".join(solved))
                    last = group[-1].end()
                    emit = False
        if emit:
            out.append(text[last:m.end()])
            last = m.end()
        i = j + 1
    out.append(text[last:])
    return "".join(out)


def is_caps(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.6


def parse(pdf: Path, header_re: str) -> dict[str, dict]:
    reader = pymupdf.open(str(pdf))
    re_header = re.compile(header_re)
    rules: dict[str, dict] = {}
    current = None
    rule_no = None

    def close():
        nonlocal current
        if current is not None:
            # при дубликатах (оглавление vs тело документа) берём запись с большей
            # страницей — тело идёт после оглавления
            old = rules.get(current["key"])
            if old is None or current["page"] > old["page"]:
                rules[current["key"]] = {
                    "title": fix_orphans(current["title"].strip(" –—-")),
                    "page": current["page"],
                    "text": fix_orphans("\n".join(current["text"]).strip()),
                }
        current = None

    # проход 1: якорь начала тела документа — последняя строка «ПРАВИЛО 1»
    # в первой половине файла (оглавление идёт раньше, подписи в приложениях — позже)
    re_rule1 = re.compile(r"^ПРАВИЛО\s+1\b")
    anchor = -1
    pages = [p.get_text() or "" for p in reader]
    for i, text in enumerate(pages[: len(pages) // 2]):
        if any(re_rule1.match(re.sub(r"\s+", " ", ln).strip()) for ln in text.split("\n")):
            anchor = i

    # колонтитулы: капс-строки у края страницы (первые/последние 2 строки),
    # повторяющиеся на ≥3 страницах — бегущие заголовки разделов
    edge: dict[str, set[int]] = {}
    for i, text in enumerate(pages):
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
        lines = [ln for ln in lines if ln]
        for ln in lines[:3] + lines[-3:]:
            edge.setdefault(ln, set()).add(i)
    running = {
        ln for ln, pgs in edge.items()
        if len(pgs) >= 3 and len(ln) >= 8 and is_caps(ln)
        and not (RE_RULE.match(ln) or RE_SUB.match(ln))
    }

    # проход 2: номера правил идут строго по возрастанию — отсекает и
    # дубли из оглавления, и подписи «ПРАВИЛО N» в разделах жестов/приложений
    last_rule = 0
    skip_divider = False  # блок страницы-разделителя после строки разрядки
    for page_idx, text in enumerate(pages):
        if page_idx < anchor:
            continue
        raw_lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
        # «ПРАВИЛО» и номер могут извлекаться отдельными строками
        merged_lines: list[str] = []
        i = 0
        while i < len(raw_lines):
            ln = raw_lines[i]
            nxt = raw_lines[i + 1] if i + 1 < len(raw_lines) else ""
            if ln == "ПРАВИЛО" and re.match(r"^\d{1,3}\b", nxt):
                merged_lines.append(f"ПРАВИЛО {nxt}")
                i += 2
            elif re.fullmatch(r"\d{1,3}\.\d{1,2}\.?", ln) and nxt:
                merged_lines.append(f"{ln} {nxt}")
                i += 2
            else:
                merged_lines.append(ln)
                i += 1
        for line in merged_lines:
            if RE_SPACED.match(line or "") or RE_SECTION_HDR.match(line or ""):
                skip_divider = True
                continue
            if skip_divider:
                # баннер раздела/колонтитул: «·», строки капсом, пустые —
                # до первой строки со строчными буквами; заголовки правил
                # не проглатываем
                if (
                    not line
                    or RE_BULLET.match(line)
                    or (is_caps(line) and not (RE_RULE.match(line) or RE_SUB.match(line)))
                ):
                    continue
                skip_divider = False
            if (
                not line
                or RE_PAGE_NUM.match(line)
                or re_header.match(line)
                or line.startswith("➔")
                or RE_BULLET.match(line)
                or line in running
            ):
                continue
            m_rule = RE_RULE.match(line)
            m_sub = RE_SUB.match(line)
            # «ПРАВИЛО N» вне возрастающей последовательности — подписи из
            # разделов жестов/приложений, в текст не добавляем
            if m_rule and int(m_rule.group(1)) <= last_rule:
                continue
            if m_rule:
                last_rule = int(m_rule.group(1))
                close()
                rule_no = m_rule.group(1)
                current = {"key": rule_no, "title": m_rule.group(2), "page": page_idx + 1, "text": []}
                continue
            if m_sub and is_caps(m_sub.group(3)) and (rule_no is None or m_sub.group(1) == rule_no):
                close()
                current = {
                    "key": f"{m_sub.group(1)}.{m_sub.group(2)}",
                    "title": m_sub.group(3),
                    "page": page_idx + 1,
                    "text": [],
                }
                continue
            if current is not None:
                # продолжение многострочного заголовка капсом; разделители
                # («–», «/») тоже относятся к заголовку, пока текст не начался
                if not current["text"] and (
                    is_caps(line) or not RE_TOKEN.search(line)
                ):
                    current["title"] += " " + line
                else:
                    current["text"].append(line)
    close()

    # второй проход: склейка разрядок по словарю
    for e in rules.values():
        e["title"] = merge_spaced(e["title"])
        e["text"] = merge_spaced(e["text"])
    return rules


def main() -> None:
    out = {}
    for key, doc in DOCS.items():
        if not doc["pdf"].exists():
            print(f"skip {key}: {doc['pdf']} not found", file=sys.stderr)
            continue
        rules = parse(doc["pdf"], doc["header_re"])
        dest = BASE / f"rules_{key}.json"
        dest.write_text(
            json.dumps({"url": doc["url"], "rules": rules}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"{key}: {len(rules)} rules -> {dest}")


if __name__ == "__main__":
    main()
