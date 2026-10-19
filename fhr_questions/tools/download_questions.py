#!/usr/bin/env python3
"""Скачивает все вопросы с https://referees.fhr.ru/questions через GraphQL API.

Результат:
  fhr_questions/questions.json      — сырые данные (все вопросы)
  fhr_questions/questions.md        — активные вопросы + ссылки/тексты правил
  fhr_questions/questions_deleted.md — вопросы с пометкой «Удалён» на сайте

Ссылки на правила строятся по rules_docs/rules_*.json (см. extract_rules.py):
  - Руководство по судейству ЕКП 2022/23   (нумерация совпадает с numRule)
  - Книга правил ИИХФ 2026/27              (актуальная редакция)
  - Книга правил ИИХФ 2018–2022            (старая нумерация для удалённых вопросов)
"""
import json
import re
import time
import urllib.request
from html import unescape
from pathlib import Path

API = "https://referees.fhr.ru/graphql"
BASE = Path(__file__).parent
OUT_DIR = BASE / "fhr_questions"
RULES_DIR = BASE / "rules_docs"

DOC_LABELS = {
    "ekp_2022_23": "Руководство ЕКП 2022/23",
    "iihf_2026_27": "Книга правил ИИХФ 2026/27",
    "iihf_2018_22": "Книга правил ИИХФ 2018–2022",
}
DOC_ORDER = ["ekp_2022_23", "iihf_2026_27", "iihf_2018_22"]

# Приложения Руководства ЕКП 2022/23 (номер -> (страница PDF, название))
EKP_APPENDICES = {
    1: (178, "Жесты судей"),
    2: (189, "Определения, терминология и понятия"),
    3: (198, "Снаряжение для хоккея с шайбой"),
    4: (207, "Справочные таблицы"),
    5: (236, "Технические требования"),
    6: (237, "Инфографика"),
}

QUERY = """
query QuestionsQuery($pagination: ExamPlatformPaginationInput!) {
    examPlatform {
        questionsLastUpdate
        questions(pagination: $pagination) {
            items {
                id
                active
                updatedAt
                numRule
                text
                video { id name subDir version originalName durationMs
                        videoFileGroup { moduleName entityName } }
                photo { id name subDir version originalName
                        imageFileGroup { moduleName entityName } }
            }
            pageInfo { page totalPages totalItems perPage hasNextPage }
        }
    }
}
"""


def gql(page: int, retries: int = 5) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"pagination": {"page": page}}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def media_url(kind: str, media: dict | None) -> str | None:
    """URL файла в том же формате, что использует фронтенд:
    https://referees.fhr.ru/static/files/{images|videos}/{moduleName}/{subDir}/{name}?{version}
    """
    if not media:
        return None
    group = media.get("imageFileGroup") or media.get("videoFileGroup") or {}
    module = group.get("moduleName") or ""
    if module == "fake":
        return media.get("name")
    return (
        f"https://referees.fhr.ru/static/files/{kind}/{module}/"
        f"{media.get('subDir') or ''}/{media.get('name') or ''}?{media.get('version')}"
    )


def clean_html(html: str) -> str:
    """Убирает Word-разметку, оставляет читаемый текст."""
    if not html:
        return ""
    s = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|li|ol|ul|div|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------- правила ----------

def load_rules() -> dict[str, dict]:
    """doc_key -> {"url": str, "rules": {ref: {title,page,text}}}"""
    docs = {}
    for key in DOC_ORDER:
        p = RULES_DIR / f"rules_{key}.json"
        if p.exists():
            docs[key] = json.loads(p.read_text(encoding="utf-8"))
    return docs


def numrule_refs(num_rule: str | None) -> list[str]:
    """'24.8, 63.9' -> ['24.8', '63.9']; '23.8.1' -> ['23.8'].
    Числа внутри 'Прил. N' не считаются номерами правил."""
    s = re.sub(r"Прил\.?\s*\d+", "", num_rule or "")
    refs = []
    for r in re.findall(r"\d{1,3}(?:\.\d{1,2}){0,2}", s):
        parts = r.split(".")
        refs.append(".".join(parts[:2]))
    return refs


def resolve_ref(ref: str, docs: dict) -> tuple[str, str] | None:
    """Ищем пункт правила: точное совпадение, затем верхний уровень N.
    Порядок документов: ЕКП 2022/23 -> ИИХФ 2026/27 -> ИИХФ 2018–22."""
    variants = [ref] + ([ref.split(".")[0]] if "." in ref else [])
    for v in variants:
        for d in DOC_ORDER:
            if d in docs and v in docs[d]["rules"]:
                return (d, v)
    return None


def anchor(doc_key: str, ref: str) -> str:
    return f"r-{doc_key}-{ref.replace('.', '-')}"


def build_md(items: list[dict], docs: dict, path: Path, title: str, last_update: str) -> None:
    used_rules: dict[tuple[str, str], None] = {}
    lines = [f"# {title}", f"Обновлено на сайте: {last_update}. Вопросов: {len(items)}.", ""]
    for i, q in enumerate(items, 1):
        badge = "" if q["active"] else " **[УДАЛЁН]**"
        lines.append(f"## {i}. {q.get('numRule') or '—'} (id {q['id']}){badge}")
        lines.append(clean_html(q.get("text", "")) or "—")
        for kind, key in (("images", "photo"), ("videos", "video")):
            url = media_url(kind, q.get(key))
            if url:
                lines.append(f"[{key}]({url})")
        for m in re.findall(r"Прил\.?\s*(\d)", q.get("numRule") or ""):
            n = int(m)
            if "ekp_2022_23" in docs and n in EKP_APPENDICES:
                pg, name = EKP_APPENDICES[n]
                url = f"{docs['ekp_2022_23']['url']}#page={pg}"
                lines.append(
                    f"> Приложение {n} — {name} — "
                    f"[{DOC_LABELS['ekp_2022_23']}, стр. {pg}]({url})"
                )
        refs = numrule_refs(q.get("numRule"))
        for ref in refs:
            res = resolve_ref(ref, docs)
            if not res:
                continue
            doc_key, key = res
            rule = docs[doc_key]["rules"][key]
            url = f"{docs[doc_key]['url']}#page={rule['page']}"
            used_rules[(doc_key, key)] = None
            label = DOC_LABELS.get(doc_key, doc_key)
            lines.append(
                f"> Правило {key} — {rule['title']} — "
                f"[{label}, стр. {rule['page']}]({url}) · "
                f"[текст правила](#{anchor(doc_key, key)})"
            )
        lines.append("")

    if used_rules:
        lines += ["---", "", "# Тексты правил", ""]
        order = {k: i for i, k in enumerate(DOC_ORDER)}
        for doc_key, ref in sorted(
            used_rules,
            key=lambda dk: (order.get(dk[0], 9), [int(p) for p in dk[1].split(".")]),
        ):
            rule = docs[doc_key]["rules"][ref]
            url = f"{docs[doc_key]['url']}#page={rule['page']}"
            label = DOC_LABELS.get(doc_key, doc_key)
            lines.append(f'<a name="{anchor(doc_key, ref)}"></a>')
            lines.append(f"### Правило {ref} — {rule['title']}")
            lines.append(f"*{label}, стр. {rule['page']} — [PDF]({url})*")
            lines.append("")
            lines.append(rule["text"] or "—")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    items, last_update, total_pages = [], None, 1
    page = 1
    while page <= total_pages:
        data = gql(page)
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors on page {page}: {data['errors']}")
        ep = data["data"]["examPlatform"]
        last_update = ep["questionsLastUpdate"]
        q = ep["questions"]
        items.extend(q["items"])
        info = q["pageInfo"]
        total_pages = info["totalPages"]
        print(f"page {info['page']}/{total_pages}: +{len(q['items'])} (total {len(items)}/{info['totalItems']})")
        page += 1
        if page <= total_pages:
            time.sleep(0.5)

    raw = {"questionsLastUpdate": last_update, "count": len(items), "items": items}
    (OUT_DIR / "questions.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    docs = load_rules()
    active = [q for q in items if q["active"]]
    deleted = [q for q in items if not q["active"]]
    build_md(active, docs, OUT_DIR / "questions.md", "Вопросы для судей ФХР", last_update)
    build_md(
        deleted, docs, OUT_DIR / "questions_deleted.md",
        "Вопросы для судей ФХР — УДАЛЁННЫЕ", last_update,
    )
    print(f"Saved {len(active)} active + {len(deleted)} deleted to {OUT_DIR}/")


if __name__ == "__main__":
    main()
