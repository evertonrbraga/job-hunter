#!/usr/bin/env python3
"""
vaga-gringa-bot — agregador de vagas de engenharia de software remotas
ABERTAS A BRASILEIROS, a partir de fontes com API/RSS pública.

O QUE ELE FAZ (de forma confiável):
  - Busca vagas de SWE em RemoteOK, Remotive, We Work Remotely e nas APIs
    públicas de ATS (Greenhouse / Lever / Ashby) das suas empresas-alvo.
  - Classifica cada vaga pela elegibilidade geográfica:
        incluir   -> diz worldwide/anywhere/global/brazil/latam/etc.
        excluir   -> exige autorização nos EUA, "US only", "no visa
                     sponsorship", ou trava em outra região (EMEA/EU only...)
        verificar -> não diz nada -> VOCÊ checa manualmente (LinkedIn People
                     ou página de carreiras). O script NÃO scrapeia LinkedIn.
  - Preenche salário quando a vaga tem; quando não tem, estima por
    senioridade e MARCA como estimado (coluna salario_estimado=TRUE).
  - Deduplica e ACUMULA num CSV (vagas.csv).

O QUE ELE NÃO FAZ (de propósito — ver README):
  - Não loga nem scrapeia LinkedIn / Indeed / Wellfound / Glassdoor.
    São muralhas anti-bot; automatizar dá ban e quebra. Use manual + alerta.

USO:
  Primeira passagem (última semana):
      python job_hunter.py --since-days 7
  Diário (cumulativo, via GitHub Actions):
      python job_hunter.py --since-days 1
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

import requests
import feedparser
import yaml

UA = "vaga-gringa-bot/1.0 (+busca pessoal de vagas)"
TIMEOUT = 30
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# --------------------------------------------------------------------------
# Classificação geográfica
# --------------------------------------------------------------------------

# Positivos FORTES: se aparecerem, a vaga entra mesmo que haja ruído de "US".
STRONG_INCLUDE = [
    r"\bworldwide\b", r"\banywhere\b", r"\bglobal(ly)?\b",
    r"\bbrazil\b", r"\bbrasil\b",
    r"\blat[\s\-]?am\b", r"\blatin\s*america\b", r"\bsouth\s*america\b",
    r"\bam[ée]ricas?\b", r"work from anywhere",
]

# Excludentes: claramente NÃO aceita brasileiro.
HARD_EXCLUDE = [
    # autorização / sponsorship nos EUA
    r"authoriz(e|ed|ation)\s+to\s+work\s+in\s+(the\s+)?(us|u\.s\.?|united\s+states)",
    r"(no|not|unable|cannot|do(es)?\s+not)\b[^.]{0,40}\bvisa\s+sponsor",
    r"visa\s+sponsorship\s+is\s+not",
    r"without\s+visa\s+sponsor",
    r"eligible\s+to\s+work\s+in\s+(the\s+)?(us|united\s+states)",
    r"work\s+authorization\s+in\s+(the\s+)?(us|united\s+states)",
    # trava nos EUA
    r"\bus[\s\-]?based\b", r"\bus\s*only\b", r"\busa\s*only\b",
    r"\bunited\s+states\s+only\b", r"\bus\s+residents?\b", r"\bus\s+citizens?\b",
    r"\bus\s+remote\b", r"remote\s*[\(\-,]\s*us\b", r"remote\s*-\s*united\s+states",
    r"must\s+(be\s+)?(located|based|residing|reside)\b[^.]{0,30}\b(us|united\s+states)",
    # trava em OUTRA região (também exclui o Brasil)
    r"\b(emea|eu|europe|uk|apac|canada|india)\s*[\-]?\s*only\b",
    r"remote\s*\(\s*(emea|eu|europe|uk|apac|canada|india)\b",
    r"must\s+(be\s+)?(located|based)\b[^.]{0,30}\b(emea|europe|uk|eu)\b",
]

STRONG_INCLUDE_RE = [re.compile(p, re.I) for p in STRONG_INCLUDE]
HARD_EXCLUDE_RE = [re.compile(p, re.I) for p in HARD_EXCLUDE]


def classify_location(text: str) -> str:
    """Retorna 'incluir' | 'excluir' | 'verificar'."""
    t = " ".join((text or "").split()).lower()
    if any(r.search(t) for r in STRONG_INCLUDE_RE):
        return "incluir"
    if any(r.search(t) for r in HARD_EXCLUDE_RE):
        return "excluir"
    return "verificar"


# --------------------------------------------------------------------------
# Estimativa de salário (rotulada). Faixas em USD/ano, contexto contractor LatAm.
# Baseado em medianas de mercado (ex.: Arc.dev remote salary, levels.fyi).
# --------------------------------------------------------------------------

def estimate_salary(title: str) -> str:
    t = (title or "").lower()
    if any(k in t for k in ["staff", "principal", "lead", "architect", "head of", "director"]):
        return "~US$90k–130k"
    if any(k in t for k in ["senior", "sr.", "sr ", "snr", "sênior", "senior "]):
        return "~US$60k–90k"
    if any(k in t for k in ["junior", "jr.", "jr ", "intern", "entry", "trainee", "estagi"]):
        return "~US$25k–45k"
    return "~US$45k–75k"  # pleno / não especificado


# --------------------------------------------------------------------------
# Filtro de cargo (é vaga de eng. de software?)
# --------------------------------------------------------------------------

def is_software_role(title: str, software_titles: list[str]) -> bool:
    t = (title or "").lower()
    return any(s in t for s in software_titles)


# --------------------------------------------------------------------------
# Datas
# --------------------------------------------------------------------------

def parse_date(value) -> Optional[dt.datetime]:
    """Aceita ISO (com Z), epoch (s ou ms) e struct_time. Retorna UTC aware."""
    if value is None or value == "":
        return None
    # struct_time (feedparser)
    if hasattr(value, "tm_year"):
        import calendar
        return dt.datetime.fromtimestamp(calendar.timegm(value), tz=dt.timezone.utc)
    # número (epoch)
    try:
        n = float(value)
        if n > 1e12:      # ms
            n /= 1000.0
        return dt.datetime.fromtimestamp(n, tz=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    # string ISO
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                d = dt.datetime.strptime(s, fmt)
                return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------

@dataclass
class Job:
    empresa: str
    cargo: str
    salario: str
    salario_estimado: str   # "TRUE"/"FALSE"
    data_publicacao: str    # YYYY-MM-DD
    pais: str               # texto bruto de localização
    fonte: str
    url: str
    status_localizacao: str # incluida / verificar
    id: str


def make_job(empresa, cargo, salary_raw, location_text, date, fonte, url,
             extra_text="") -> Optional[Job]:
    status = classify_location(f"{cargo} {location_text} {extra_text}")
    if status == "excluir":
        return None
    if salary_raw:
        salario, estimado = str(salary_raw).strip(), "FALSE"
    else:
        salario, estimado = estimate_salary(cargo), "TRUE"
    d = parse_date(date)
    jid = hashlib.sha1(f"{fonte}|{url}".encode()).hexdigest()[:16]
    return Job(
        empresa=(empresa or "").strip(),
        cargo=(cargo or "").strip(),
        salario=salario,
        salario_estimado=estimado,
        data_publicacao=d.date().isoformat() if d else "",
        pais=(location_text or "").strip()[:120],
        fonte=fonte,
        url=url,
        status_localizacao="incluida" if status == "incluir" else "verificar",
        id=jid,
    )


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


# --------------------------------------------------------------------------
# Fontes
# --------------------------------------------------------------------------

def fetch_remoteok(software_titles) -> Iterable[Job]:
    r = requests.get("https://remoteok.com/api", headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    for item in r.json():
        if not isinstance(item, dict) or "position" not in item:
            continue  # primeiro elemento é metadata/legal
        title = item.get("position", "")
        if not is_software_role(title, software_titles):
            continue
        sal = ""
        if item.get("salary_min") and item.get("salary_max"):
            sal = f"US${int(item['salary_min'])//1000}k–{int(item['salary_max'])//1000}k"
        loc = item.get("location") or " ".join(item.get("tags", []))
        job = make_job(item.get("company"), title, sal, loc,
                       item.get("date") or item.get("epoch"),
                       "RemoteOK", item.get("url") or item.get("apply_url", ""),
                       extra_text=" ".join(item.get("tags", [])))
        if job:
            yield job


def fetch_remotive(software_titles) -> Iterable[Job]:
    r = requests.get("https://remotive.com/api/remote-jobs?category=software-dev",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        if not is_software_role(title, software_titles):
            continue
        loc = j.get("candidate_required_location", "")
        job = make_job(j.get("company_name"), title, j.get("salary", ""), loc,
                       j.get("publication_date"), "Remotive", j.get("url", ""),
                       extra_text=" ".join(j.get("tags", [])))
        if job:
            yield job


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
]


def fetch_wwr(software_titles) -> Iterable[Job]:
    for feed_url in WWR_FEEDS:
        feed = feedparser.parse(feed_url, agent=UA)
        for e in feed.entries:
            raw = e.get("title", "")
            empresa, _, cargo = raw.partition(":")
            if not cargo:
                empresa, cargo = "", raw
            if not is_software_role(cargo, software_titles):
                continue
            region = e.get("region", "") or ""
            desc = clean(e.get("summary", ""))
            job = make_job(empresa.strip(), cargo.strip(), "", region or "n/d",
                           e.get("published_parsed"), "WeWorkRemotely",
                           e.get("link", ""), extra_text=desc[:500])
            if job:
                yield job


def fetch_greenhouse(name, slug, software_titles) -> Iterable[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        if not is_software_role(title, software_titles):
            continue
        loc = (j.get("location") or {}).get("name", "")
        content = clean(j.get("content", ""))[:800]
        job = make_job(name, title, "", loc, j.get("updated_at"),
                       f"{name} (Greenhouse)", j.get("absolute_url", ""),
                       extra_text=content)
        if job:
            yield job


def fetch_lever(name, slug, software_titles) -> Iterable[Job]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json():
        title = j.get("text", "")
        if not is_software_role(title, software_titles):
            continue
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "")
        job = make_job(name, title, "", loc, j.get("createdAt"),
                       f"{name} (Lever)", j.get("hostedUrl", ""),
                       extra_text=clean(j.get("descriptionPlain", ""))[:800])
        if job:
            yield job


def fetch_ashby(name, slug, software_titles) -> Iterable[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        title = j.get("title", "")
        if not is_software_role(title, software_titles):
            continue
        loc = j.get("location", "") or (j.get("address") or {}).get("postalAddress", "")
        sal = ""
        comp = j.get("compensation") or {}
        if isinstance(comp, dict):
            sal = comp.get("compensationTierSummary", "") or ""
        date = j.get("publishedDate") or j.get("publishedAt") or j.get("updatedAt")
        job = make_job(name, title, sal, loc, date,
                       f"{name} (Ashby)", j.get("jobUrl", ""))
        if job:
            yield job


ATS_DISPATCH = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


# --------------------------------------------------------------------------
# Coleta + escrita
# --------------------------------------------------------------------------

FIELDNAMES = ["empresa", "cargo", "salario", "salario_estimado",
              "data_publicacao", "pais", "fonte", "url",
              "status_localizacao", "id"]


def collect(config, since_days) -> list[Job]:
    titles = [s.lower() for s in config.get("software_titles", [])]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    jobs: list[Job] = []

    sources = config.get("sources", {})
    runners = []
    if sources.get("remoteok"):
        runners.append(("RemoteOK", lambda: fetch_remoteok(titles)))
    if sources.get("remotive"):
        runners.append(("Remotive", lambda: fetch_remotive(titles)))
    if sources.get("weworkremotely"):
        runners.append(("WeWorkRemotely", lambda: fetch_wwr(titles)))
    for c in config.get("ats_companies", []):
        fn = ATS_DISPATCH.get((c.get("ats") or "").lower())
        if fn:
            runners.append((c["name"],
                            (lambda f=fn, c=c: f(c["name"], c["slug"], titles))))

    for label, runner in runners:
        try:
            n = 0
            for job in runner():
                if not job.url:
                    continue
                d = parse_date(job.data_publicacao)
                # sem data confiável (ex.: alguns ATS) -> mantém (não dá pra filtrar)
                if d is not None and d < cutoff:
                    continue
                jobs.append(job)
                n += 1
            print(f"  [ok] {label}: {n} vaga(s) elegível(is)")
        except Exception as exc:  # uma fonte falhar não derruba o resto
            print(f"  [WARN] {label} falhou: {exc}", file=sys.stderr)
    return jobs


def write_csv(jobs: list[Job], out: Path) -> int:
    existing_ids = set()
    rows = []
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_ids.add(row.get("id"))
                rows.append(row)
    added = 0
    for j in jobs:
        if j.id in existing_ids:
            continue
        rows.append(asdict(j))
        existing_ids.add(j.id)
        added += 1
    rows.sort(key=lambda r: (r.get("data_publicacao") or "", r.get("empresa") or ""),
              reverse=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    return added


def main():
    ap = argparse.ArgumentParser(description="Agregador de vagas de SWE para brasileiros")
    ap.add_argument("--since-days", type=int, default=1,
                    help="janela em dias (7 na primeira passagem, 1 no diário)")
    ap.add_argument("--out", default="vagas.csv")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    print(f"Coletando vagas dos últimos {args.since_days} dia(s)...")
    jobs = collect(config, args.since_days)
    added = write_csv(jobs, Path(args.out))

    inc = sum(1 for j in jobs if j.status_localizacao == "incluida")
    ver = sum(1 for j in jobs if j.status_localizacao == "verificar")
    print(f"\nResumo: {len(jobs)} coletadas | {inc} incluídas | "
          f"{ver} a VERIFICAR | {added} novas adicionadas a {args.out}")


if __name__ == "__main__":
    main()
