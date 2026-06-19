#!/usr/bin/env python3
"""
vaga-gringa-bot v2 — agregador de vagas de engenharia de software ABERTAS A
BRASILEIROS, varrendo o máximo de fontes públicas (API/RSS) possível.

COBRE (todas com API/feed público e estável):
  Boards remotos internacionais (US$ / mundo todo):
    - RemoteOK, Remotive (várias categorias), We Work Remotely (vários feeds),
      Himalayas (~86k vagas, com restrição de localização estruturada),
      Jobicy (filtro por geografia), Arbeitnow, The Muse.
  Vagas brasileiras (R$):
    - Gupy (maior ATS do Brasil), Programathor.
  Direto na fonte (ATS das empresas-alvo):
    - Greenhouse, Lever, Ashby, SmartRecruiters.
  Comunidade:
    - Hacker News "Who is hiring" (thread mensal via API do Algolia).

CLASSIFICAÇÃO GEOGRÁFICA (o coração do filtro):
    incluida  -> a vaga diz worldwide/anywhere/global/latam/brazil, OU a fonte é
                 brasileira, OU a restrição estruturada de local inclui o Brasil.
    verificar -> não diz nada -> entra para você checar à mão (LinkedIn People /
                 página de carreiras). O script NÃO scrapeia LinkedIn.
    (excluida)-> exige autorização nos EUA / "US only" / "no visa sponsorship" /
                 travada em outra região (EMEA/EU/UK/APAC/Canadá/Índia...) ou a
                 restrição estruturada lista só países sem o Brasil -> DESCARTADA.

Fontes "barulhentas" e globais (The Muse, Arbeitnow, Hacker News) entram em modo
ESTRITO: só guardam vagas com sinal explícito de elegibilidade (incluida), para
não inundar a planilha com vagas claramente de outra região.

USO:
    python job_hunter.py --since-days 7      # primeira passagem (última semana)
    python job_hunter.py --since-days 1      # diário (cumulativo, via Actions)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests
import feedparser
import yaml

UA = "Mozilla/5.0 (compatible; vaga-gringa-bot/2.0; +busca pessoal de vagas)"
TIMEOUT = 30
HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def GET(url: str, **kw) -> requests.Response:
    r = SESSION.get(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


# ==========================================================================
# 1. Classificação geográfica
# ==========================================================================

# Positivos FORTES (texto livre): se aparecerem, a vaga entra.
STRONG_INCLUDE = [
    r"\bworldwide\b", r"\banywhere\b", r"\bglobal(ly)?\b",
    r"\bbrazil\b", r"\bbrasil\b",
    r"\blat[\s\-]?am\b", r"\blatin\s*america\b", r"\bam[ée]rica\s+latina\b",
    r"\bsouth\s*america\b", r"\bam[ée]rica\s+do\s+sul\b",
    r"work\s+from\s+anywhere", r"remote\s*[\-,(]?\s*(global|worldwide|anywhere)",
    r"no\s+location\s+restriction", r"any\s+(country|location|timezone)",
    r"americas?(?!\s*[/,-]?\s*(north|n\b))",   # "Americas" mas não "North America"
]

# Excludentes: claramente NÃO aceita brasileiro.
HARD_EXCLUDE = [
    r"authoriz(e|ed|ation)\s+to\s+work\s+in\s+(the\s+)?(us|u\.s\.?|united\s+states|canada|uk|eu|europe)",
    r"(no|not|unable|cannot|do(es)?\s+not|won'?t)\b[^.]{0,40}\bvisa\s+sponsor",
    r"visa\s+sponsorship\s+is\s+not", r"without\s+visa\s+sponsor",
    r"eligible\s+to\s+work\s+in\s+(the\s+)?(us|united\s+states|uk|eu|canada)",
    r"work\s+authorization\s+in\s+(the\s+)?(us|united\s+states|canada|uk|eu)",
    r"\bus[\s\-]?based\b", r"\bus\s*[\-]?\s*only\b", r"\busa\s*only\b",
    r"\bunited\s+states\s+only\b", r"\bus\s+residents?\b", r"\bus\s+citizens?\b",
    r"\bus\s+remote\b", r"remote\s*[\(\-,]\s*us(a|\b)", r"remote\s*-\s*united\s+states",
    r"must\s+(be\s+)?(located|based|residing|reside|live)\b[^.]{0,30}\b(us|u\.s\.?|united\s+states)\b",
    # travado em OUTRA região (também exclui o Brasil)
    r"\b(emea|eu|europe|european\s+union|uk|united\s+kingdom|apac|canada|india|germany|ireland|poland|portugal|spain|france|netherlands|australia|philippines|nigeria|south\s+africa)\s*[\-]?\s*only\b",
    r"remote\s*[\(\-,]\s*(emea|eu|europe|uk|apac|canada|india)\b",
    r"must\s+(be\s+)?(located|based|residing|reside|live)\b[^.]{0,30}\b(emea|europe|european|uk|eu|apac|canada|india|germany|poland)\b",
    r"\b(eu|emea|europe)\s+time\s*zones?\s+only\b",
]

STRONG_INCLUDE_RE = [re.compile(p, re.I) for p in STRONG_INCLUDE]
HARD_EXCLUDE_RE = [re.compile(p, re.I) for p in HARD_EXCLUDE]

# Menção EXPLÍCITA ao Brasil/LatAm: sinal mais forte de todos.
BRAZIL_RE = re.compile(
    r"\bbrazil\b|\bbrasil\b|\blat[\s\-]?am\b|latin\s*america|am[ée]rica\s+latina|"
    r"south\s*america|am[ée]rica\s+do\s+sul", re.I)

# Para localizações ESTRUTURADAS (listas de país/região vindas das APIs).
# NÃO inclui "remote" sozinho — "Remote - United Kingdom" NÃO é elegível ao Brasil.
STRUCT_OK_RE = re.compile(
    r"brazil|brasil|south\s*america|am[ée]rica\s+do\s+sul|latin\s*america|"
    r"am[ée]rica\s+latina|\blat[\s\-]?am\b|worldwide|anywhere|\bglobal\b|americas", re.I)
STRUCT_NORTH_ONLY_RE = re.compile(r"^\s*north\s+americ", re.I)
# "Remoto genérico" sem lugar nomeado -> ambíguo (verificar), não exclui nem inclui.
GENERIC_REMOTE_RE = re.compile(
    r"^(100%\s*)?(fully\s+)?(remote|hybrid|flex(ible)?|distributed|"
    r"home[\s\-]*based|wfh|work\s+from\s+home|telecommute|n/d)[\s/,\-]*$", re.I)


def _has(res, text) -> bool:
    t = " ".join((text or "").split())
    return any(r.search(t) for r in res)


def classify_structured(locs) -> str:
    """Lista vazia -> sem restrição -> incluir. Casa Brasil/LatAm/worldwide ->
    incluir. Só 'Remote' genérico -> verificar. Lista lugares específicos sem o
    Brasil -> excluir."""
    items = [str(x).strip() for x in (locs or []) if str(x).strip()]
    if not items:
        return "incluir"
    if any(STRUCT_OK_RE.search(x) for x in items if not STRUCT_NORTH_ONLY_RE.match(x)):
        return "incluir"
    if all(GENERIC_REMOTE_RE.match(x) for x in items):
        return "verificar"
    return "excluir"


def classify(inc_text: str, exc_text: str = "", structured=None) -> str:
    """`inc_text` (curto: título+localização) decide a INCLUSÃO; `exc_text`
    (longo: +descrição) só acrescenta sinal de EXCLUSÃO — assim a descrição não
    inclui vaga por engano (ex.: "global teams" num cargo US-only). Brasil/LatAm
    no título/local vence a restrição estruturada."""
    exc_text = exc_text or inc_text
    brazil_loc = bool(BRAZIL_RE.search(inc_text))
    hard_exc = _has(HARD_EXCLUDE_RE, exc_text)
    s = classify_structured(structured) if structured is not None else None

    if hard_exc and not brazil_loc:
        return "excluir"
    if brazil_loc:
        return "incluir"
    if s == "incluir":
        return "incluir"
    if s == "excluir":
        return "excluir"
    if _has(STRONG_INCLUDE_RE, inc_text):   # s é None/verificar -> decide o texto
        return "incluir"
    return "verificar"


# ==========================================================================
# 2. Moeda, salário, senioridade, modelo de trabalho
# ==========================================================================

def detect_currency(*texts, default="") -> str:
    t = " ".join(str(x) for x in texts if x)
    up = t.upper()
    if "R$" in t or "BRL" in up or "REAIS" in up:
        return "BRL"
    if "US$" in t or "USD" in up:
        return "USD"
    if "€" in t or "EUR" in up:
        return "EUR"
    if "£" in t or "GBP" in up:
        return "GBP"
    if "$" in t:
        return "USD"
    return default


def seniority_of(title: str, hint=None) -> str:
    """Rótulo de senioridade. Prefere o sinal estruturado (hint) quando há."""
    if hint:
        h = " ".join(hint).lower() if isinstance(hint, (list, tuple)) else str(hint).lower()
        if any(k in h for k in ["manager", "lead", "principal", "staff", "head", "director"]):
            return "Liderança/Staff"
        if "senior" in h or "sênior" in h or "sr" in h:
            return "Sênior"
        if "mid" in h or "pleno" in h or "intermediate" in h:
            return "Pleno"
        if any(k in h for k in ["junior", "júnior", "entry", "intern", "graduate", "trainee", "estag"]):
            return "Júnior"
    t = (title or "").lower()
    if any(k in t for k in ["staff", "principal", "lead", "architect", "head of", "director", "vp ", "líder", "lider", "tech lead", "techlead"]):
        return "Liderança/Staff"
    if any(k in t for k in ["senior", "sr.", "sr ", "snr", "sênior", "senior "]):
        return "Sênior"
    if any(k in t for k in ["junior", "jr.", "jr ", "júnior", "intern", "entry", "trainee", "estagi", "estág"]):
        return "Júnior"
    if any(k in t for k in ["pleno", "mid-level", "mid level", "intermediári"]):
        return "Pleno"
    return ""


# Faixas medianas de mercado. USD/ano (contractor LatAm); BRL/mês (CLT/PJ BR).
EST_USD = {"Liderança/Staff": "~US$90k–140k/ano", "Sênior": "~US$65k–95k/ano",
           "Pleno": "~US$45k–70k/ano", "Júnior": "~US$25k–45k/ano",
           "": "~US$45k–75k/ano"}
EST_BRL = {"Liderança/Staff": "~R$18k–30k/mês", "Sênior": "~R$12k–20k/mês",
           "Pleno": "~R$8k–14k/mês", "Júnior": "~R$4k–8k/mês",
           "": "~R$8k–15k/mês"}


def estimate_salary(seniority: str, brl: bool) -> str:
    return (EST_BRL if brl else EST_USD).get(seniority, EST_USD[""])


WORKPLACE_MAP = {
    "remote": "Remoto", "remoto": "Remoto", "fully remote": "Remoto",
    "hybrid": "Híbrido", "híbrido": "Híbrido", "hibrido": "Híbrido",
    "on-site": "Presencial", "onsite": "Presencial", "on_site": "Presencial",
    "presencial": "Presencial", "office": "Presencial",
}


def model_of(workplace="", text="", default="Remoto") -> str:
    w = (workplace or "").strip().lower()
    if w in WORKPLACE_MAP:
        return WORKPLACE_MAP[w]
    low = (text or "").lower()
    if "hybrid" in low or "híbrid" in low:
        return "Híbrido"
    if "on-site" in low or "presencial" in low or "in office" in low:
        return "Presencial"
    return default


# ==========================================================================
# 3. Datas e limpeza
# ==========================================================================

def parse_date(value) -> Optional[dt.datetime]:
    if value is None or value == "":
        return None
    if hasattr(value, "tm_year"):           # struct_time (feedparser)
        import calendar
        return dt.datetime.fromtimestamp(calendar.timegm(value), tz=dt.timezone.utc)
    try:                                    # epoch (s ou ms)
        n = float(value)
        if n > 1e12:
            n /= 1000.0
        return dt.datetime.fromtimestamp(n, tz=dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    s = str(value).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%a, %d %b %Y %H:%M:%S %z",
                    "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                d = dt.datetime.strptime(s, fmt)
                return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    return None


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def is_software_role(title: str, software_titles: list[str]) -> bool:
    t = (title or "").lower()
    return any(s in t for s in software_titles)


def norm_key(empresa: str, cargo: str) -> str:
    """Chave de deduplicação entre fontes: empresa+cargo normalizados."""
    base = f"{empresa}|{cargo}".lower()
    base = re.sub(r"[^a-z0-9]+", " ", base).strip()
    return base


# ==========================================================================
# 4. Modelo
# ==========================================================================

@dataclass
class Job:
    empresa: str
    cargo: str
    senioridade: str
    modelo: str
    salario: str
    moeda: str
    salario_estimado: str   # "TRUE"/"FALSE"
    data_publicacao: str    # YYYY-MM-DD
    pais: str
    fonte: str
    url: str
    status_localizacao: str  # incluida / verificar
    id: str
    _dt: Optional[dt.datetime] = field(default=None, repr=False, compare=False)


def make_job(empresa, cargo, *, salary_raw="", currency="", location_text="",
             structured=None, date=None, fonte="", url="", extra_text="",
             workplace="", seniority_hint=None, br=False, strict=False) -> Optional[Job]:
    if not url:
        return None
    inc_text = f"{cargo} {location_text}"
    if br:
        status = "incluir"
    else:
        status = classify(inc_text, f"{inc_text} {extra_text}", structured)
    if status == "excluir":
        return None
    if strict and status != "incluir":
        return None

    sen = seniority_of(cargo, seniority_hint)
    moeda = currency or detect_currency(salary_raw, location_text, default="BRL" if br else "")
    if salary_raw:
        salario, estimado = str(salary_raw).strip(), "FALSE"
        if not moeda:
            moeda = detect_currency(salario, default="USD")
    else:
        salario, estimado = estimate_salary(sen, brl=br), "TRUE"
        moeda = "BRL" if br else (moeda or "USD")

    d = parse_date(date)
    jid = hashlib.sha1(f"{fonte}|{url}".encode()).hexdigest()[:16]
    pais = (location_text or (", ".join(structured) if structured else "")).strip()
    return Job(
        empresa=(empresa or "").strip(), cargo=(cargo or "").strip(),
        senioridade=sen, modelo=model_of(workplace, inc_text),
        salario=salario, moeda=moeda, salario_estimado=estimado,
        data_publicacao=d.date().isoformat() if d else "",
        pais=pais[:120], fonte=fonte, url=url,
        status_localizacao="incluida" if status == "incluir" else "verificar",
        id=jid, _dt=d,
    )


# ==========================================================================
# 5. Fontes — boards internacionais
# ==========================================================================

def fetch_remoteok(titles, **_) -> Iterable[Job]:
    for item in GET("https://remoteok.com/api").json():
        if not isinstance(item, dict) or "position" not in item:
            continue
        title = item.get("position", "")
        if not is_software_role(title, titles):
            continue
        sal = ""
        if item.get("salary_min") and item.get("salary_max"):
            sal = f"US${int(item['salary_min'])//1000}k–{int(item['salary_max'])//1000}k"
        loc = item.get("location") or ""
        tags = " ".join(item.get("tags", []))
        job = make_job(item.get("company"), title, salary_raw=sal, currency="USD",
                       location_text=loc, structured=[loc] if loc.strip() else None,
                       date=item.get("date") or item.get("epoch"),
                       fonte="RemoteOK", url=item.get("url") or item.get("apply_url", ""),
                       extra_text=tags)
        if job:
            yield job


REMOTIVE_CATS = ["software-dev", "devops-sysadmin", "data", "qa"]


def fetch_remotive(titles, **_) -> Iterable[Job]:
    seen = set()
    for cat in REMOTIVE_CATS:
        try:
            data = GET(f"https://remotive.com/api/remote-jobs?category={cat}").json()
        except Exception:
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if j.get("url") in seen or not is_software_role(title, titles):
                continue
            seen.add(j.get("url"))
            loc = j.get("candidate_required_location", "")
            job = make_job(j.get("company_name"), title, salary_raw=j.get("salary", ""),
                           location_text=loc, structured=[loc] if loc else None,
                           date=j.get("publication_date"), fonte="Remotive",
                           url=j.get("url", ""), extra_text=" ".join(j.get("tags", [])))
            if job:
                yield job


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
]


def fetch_wwr(titles, **_) -> Iterable[Job]:
    for feed_url in WWR_FEEDS:
        try:
            raw = GET(feed_url, headers={"Accept": "application/rss+xml,*/*"}).content
        except Exception:
            continue
        for e in feedparser.parse(raw).entries:
            raw_title = e.get("title", "")
            empresa, _, cargo = raw_title.partition(":")
            if not cargo:
                empresa, cargo = "", raw_title
            if not is_software_role(cargo, titles):
                continue
            region = e.get("region", "") or ""
            desc = clean(e.get("summary", ""))[:500]
            # o campo region do WWR é explícito ("Anywhere in the World",
            # "Latin America Only", "USA Only"...) -> ótimo sinal estruturado.
            job = make_job(empresa.strip(), cargo.strip(), location_text=region or "n/d",
                           structured=[region] if region.strip() else None,
                           date=e.get("published_parsed"), fonte="WeWorkRemotely",
                           url=e.get("link", ""), extra_text=desc)
            if job:
                yield job


def fetch_himalayas(titles, cutoff=None, **_) -> Iterable[Job]:
    # A API devolve ~20/página (ignora limit>20). Avançamos pelo tamanho real
    # da página e paramos ao passar da janela de datas (vem ordenada desc).
    offset, max_pages = 0, 80
    for _ in range(max_pages):
        data = GET(f"https://himalayas.app/jobs/api?limit=100&offset={offset}").json()
        jobs = data.get("jobs", [])
        if not jobs:
            break
        oldest = None
        for j in jobs:
            d = parse_date(j.get("pubDate"))
            if d and (oldest is None or d < oldest):
                oldest = d
            title = j.get("title", "")
            cats = " ".join(j.get("categories", []) + j.get("parentCategories", []))
            if not (is_software_role(title, titles) or
                    any(k in cats.lower() for k in ["developer", "engineer", "software", "devops", "data"])):
                continue
            sal = ""
            if j.get("minSalary") and j.get("maxSalary"):
                per = j.get("salaryPeriod", "annual")
                suf = "/ano" if per == "annual" else f"/{per}"
                sal = f"{int(j['minSalary'])//1000}k–{int(j['maxSalary'])//1000}k{suf}"
            job = make_job(j.get("companyName"), title, salary_raw=sal,
                           currency=j.get("currency") or "", structured=j.get("locationRestrictions"),
                           location_text=", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                           date=j.get("pubDate"), fonte="Himalayas",
                           url=j.get("applicationLink") or j.get("guid", ""),
                           seniority_hint=j.get("seniority"))
            if job:
                yield job
        if cutoff and oldest and oldest < cutoff:
            break  # API vem ordenada por data desc; passou da janela
        offset += len(jobs)
        time.sleep(0.1)  # educado com a API entre páginas


JOBICY_CALLS = [
    "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev",
    "https://jobicy.com/api/v2/remote-jobs?count=100&geo=brazil",
    "https://jobicy.com/api/v2/remote-jobs?count=100&geo=latin-america",
    "https://jobicy.com/api/v2/remote-jobs?count=100&geo=anywhere",
]


def fetch_jobicy(titles, **_) -> Iterable[Job]:
    seen = set()
    for url in JOBICY_CALLS:
        try:
            data = GET(url).json()
        except Exception:
            continue
        for j in data.get("jobs", []):
            jid = j.get("id")
            title = j.get("jobTitle", "")
            if jid in seen or not is_software_role(title, titles):
                continue
            seen.add(jid)
            geo = j.get("jobGeo", "") or ""
            job = make_job(j.get("companyName"), title, structured=[geo] if geo else None,
                           location_text=geo, date=j.get("pubDate"), fonte="Jobicy",
                           url=j.get("url", ""), seniority_hint=j.get("jobLevel"),
                           extra_text=clean(j.get("jobExcerpt", ""))[:300])
            if job:
                yield job


def fetch_arbeitnow(titles, cutoff=None, **_) -> Iterable[Job]:
    # Board majoritariamente europeu -> ESTRITO (só worldwide explícito entra).
    for page in range(1, 4):
        try:
            data = GET(f"https://www.arbeitnow.com/api/job-board-api?page={page}").json()
        except Exception:
            break
        rows = data.get("data", [])
        if not rows:
            break
        oldest = None
        for j in rows:
            d = parse_date(j.get("created_at"))
            if d and (oldest is None or d < oldest):
                oldest = d
            title = j.get("title", "")
            if not is_software_role(title, titles):
                continue
            loc = j.get("location", "")
            tags = " ".join(j.get("tags", []))
            job = make_job(j.get("company_name"), title, location_text=loc,
                           date=j.get("created_at"), fonte="Arbeitnow",
                           url=j.get("url", ""), extra_text=tags,
                           workplace="remote" if j.get("remote") else "", strict=True)
            if job:
                yield job
        if cutoff and oldest and oldest < cutoff:
            break


MUSE_CATS = ["Software Engineering", "Engineering", "Data Science", "Data and Analytics"]


def fetch_themuse(titles, cutoff=None, **_) -> Iterable[Job]:
    # Global e preso a cidades -> ESTRITO.
    cat_q = "".join(f"&category={c.replace(' ', '+')}" for c in MUSE_CATS)
    for page in range(0, 4):
        try:
            data = GET(f"https://www.themuse.com/api/public/jobs?page={page}{cat_q}").json()
        except Exception:
            break
        rows = data.get("results", [])
        if not rows:
            break
        oldest = None
        for j in rows:
            d = parse_date(j.get("publication_date"))
            if d and (oldest is None or d < oldest):
                oldest = d
            title = j.get("name", "")
            if not is_software_role(title, titles):
                continue
            locs = [l.get("name", "") for l in j.get("locations", [])]
            comp = (j.get("company") or {}).get("name", "")
            lvls = [l.get("name", "") for l in j.get("levels", [])]
            job = make_job(comp, title, structured=locs, location_text=", ".join(locs),
                           date=j.get("publication_date"), fonte="The Muse",
                           url=(j.get("refs") or {}).get("landing_page", ""),
                           seniority_hint=lvls, strict=True)
            if job:
                yield job
        if cutoff and oldest and oldest < cutoff:
            break


# ==========================================================================
# 6. Fontes — Brasil (R$)
# ==========================================================================

GUPY_KEYWORDS = ["desenvolvedor", "engenheiro de software", "programador",
                 "software engineer", "developer", "front-end", "back-end",
                 "full stack", "devops", "dados", "qa", "mobile"]


def fetch_gupy(titles, cutoff=None, **_) -> Iterable[Job]:
    """Maior ATS do Brasil. Busca por palavra-chave; mantém remoto/híbrido."""
    seen = set()
    for kw in GUPY_KEYWORDS:
        offset, limit = 0, 100
        for _ in range(2):  # até 200 vagas por palavra-chave
            url = (f"https://employability-portal.gupy.io/api/v1/jobs"
                   f"?jobName={requests.utils.quote(kw)}&limit={limit}&offset={offset}")
            try:
                data = GET(url).json()
            except Exception:
                break
            rows = data.get("data", [])
            if not rows:
                break
            for j in rows:
                jid = j.get("id")
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("name", "")
                if not is_software_role(title, titles):
                    continue
                wp = (j.get("workplaceType") or "").lower()
                if wp in ("on-site", "onsite", "presencial"):
                    continue  # foco em remoto/híbrido
                d = parse_date(j.get("publishedDate"))
                if cutoff and d and d < cutoff:
                    continue
                local = ", ".join(x for x in [j.get("city"), j.get("state"),
                                              j.get("country")] if x) or "Brasil"
                job = make_job(j.get("careerPageName") or "Empresa (Gupy)", title,
                               location_text=local, date=j.get("publishedDate"),
                               fonte="Gupy", url=j.get("jobUrl", ""),
                               workplace=wp, br=True)
                if job:
                    yield job
            total = (data.get("pagination") or {}).get("total", 0)
            offset += limit
            if offset >= total:
                break


# Boards de vagas BR no GitHub Issues (API REST oficial; PT-BR, R$, muito ativos).
# Todos validados em jun/2026. Dentro do Actions, GITHUB_TOKEN dá 5.000 req/h.
GITHUB_BOARDS = [
    "frontendbr/vagas", "backend-br/vagas", "react-brasil/vagas", "vuejs-br/vagas",
    "Gommunity/vagas", "CocoaHeadsBrasil/vagas", "phpdevbr/vagas",
    "brasil-php/vagas", "golang-brasil/vagas",
]


def _split_company(title: str) -> tuple[str, str]:
    """Tenta separar 'Cargo na/-/@ Empresa' dos títulos dos boards BR."""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", title)        # tira [100% Remoto] etc.
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()      # tira (Remoto) no fim
    for sep in (" na ", " @ ", " — ", " - ", " | ", " // ", " // "):
        if sep in t:
            cargo, _, emp = t.partition(sep)
            return cargo.strip(), emp.strip()[:60]
    return t, ""


def fetch_github_boards(titles, cutoff=None, **_) -> Iterable[Job]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    for repo in GITHUB_BOARDS:
        try:
            data = GET(f"https://api.github.com/repos/{repo}/issues"
                       f"?state=open&sort=created&direction=desc&per_page=100",
                       headers=headers).json()
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        board = repo.split("/")[0]
        for it in data:
            if it.get("pull_request"):
                continue
            title = it.get("title", "")
            if not is_software_role(title, titles):
                continue
            labels = [l.get("name", "") for l in it.get("labels", [])]
            low = (title + " " + " ".join(labels)).lower()
            remote = any(s in low for s in ("remoto", "remote", "home office", "home-office", "anywhere"))
            if "presencial" in low and not (remote or "híbrid" in low or "hibrid" in low):
                continue  # foco em remoto/híbrido
            d = parse_date(it.get("created_at"))
            if cutoff and d and d < cutoff:
                continue
            cargo, empresa = _split_company(title)
            wp = "hybrid" if ("híbrid" in low or "hibrid" in low) else ("remote" if remote else "")
            job = make_job(empresa, cargo, location_text="Brasil",
                           date=it.get("created_at"), fonte=f"GitHub {board}",
                           url=it.get("html_url", ""), workplace=wp,
                           seniority_hint=labels, extra_text=" ".join(labels), br=True)
            if job:
                yield job


def fetch_programathor(titles, **_) -> Iterable[Job]:
    try:
        raw = GET("https://programathor.com.br/jobs.rss",
                  headers={"Accept": "application/rss+xml,*/*"}).content
    except Exception:
        return
    for e in feedparser.parse(raw).entries:
        title = re.sub(r"^vaga:\s*", "", e.get("title", ""), flags=re.I)
        title = re.sub(r"#\S+", "", title).strip()  # tira #Sênior #FullStack
        if not is_software_role(title, titles):
            continue
        desc = clean(e.get("summary", ""))[:400]
        job = make_job("", title, location_text="Brasil", date=e.get("published_parsed"),
                       fonte="Programathor", url=e.get("link", ""), extra_text=desc, br=True)
        if job:
            yield job


# ==========================================================================
# 7. Fontes — ATS direto (Greenhouse / Lever / Ashby / SmartRecruiters)
# ==========================================================================

def fetch_greenhouse(name, slug, titles, **_) -> Iterable[Job]:
    data = GET(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true").json()
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not is_software_role(title, titles):
            continue
        loc = (j.get("location") or {}).get("name", "")
        content = clean(j.get("content", ""))[:1000]
        job = make_job(name, title, location_text=loc, structured=[loc] if loc else None,
                       date=j.get("updated_at"), fonte=f"{name} (Greenhouse)",
                       url=j.get("absolute_url", ""), extra_text=content)
        if job:
            yield job


def fetch_lever(name, slug, titles, **_) -> Iterable[Job]:
    for j in GET(f"https://api.lever.co/v0/postings/{slug}?mode=json").json():
        title = j.get("text", "")
        if not is_software_role(title, titles):
            continue
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "")
        wp = (cats.get("commitment", "") or "")
        job = make_job(name, title, location_text=loc, structured=[loc] if loc else None,
                       date=j.get("createdAt"), fonte=f"{name} (Lever)",
                       url=j.get("hostedUrl", ""), workplace=wp,
                       extra_text=clean(j.get("descriptionPlain", ""))[:1000])
        if job:
            yield job


def fetch_ashby(name, slug, titles, **_) -> Iterable[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    for j in GET(url).json().get("jobs", []):
        title = j.get("title", "")
        if not is_software_role(title, titles):
            continue
        loc = j.get("location", "") or (j.get("address") or {}).get("postalAddress", "")
        sal = ""
        comp = j.get("compensation") or {}
        if isinstance(comp, dict):
            sal = comp.get("compensationTierSummary", "") or ""
        date = j.get("publishedDate") or j.get("publishedAt") or j.get("updatedAt")
        extra = " ".join(filter(None, [j.get("departmentName", ""),
                                       j.get("teamName", ""),
                                       "remote" if j.get("isRemote") else ""]))
        job = make_job(name, title, salary_raw=sal, location_text=loc,
                       structured=[loc] if loc else None, date=date,
                       fonte=f"{name} (Ashby)", url=j.get("jobUrl", ""), extra_text=extra)
        if job:
            yield job


def fetch_smartrecruiters(name, slug, titles, **_) -> Iterable[Job]:
    offset = 0
    while offset < 300:
        data = GET(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                   f"?limit=100&offset={offset}").json()
        rows = data.get("content", [])
        if not rows:
            break
        for j in rows:
            title = j.get("name", "")
            if not is_software_role(title, titles):
                continue
            loc = j.get("location") or {}
            loctxt = ", ".join(filter(None, [loc.get("city"), loc.get("region"),
                                             loc.get("country")]))
            if loc.get("remote"):
                loctxt = (loctxt + " (remote)").strip()
            job = make_job(name, title, location_text=loctxt,
                           structured=[loctxt] if loctxt else None,
                           date=j.get("releasedDate") or j.get("createdOn"),
                           fonte=f"{name} (SmartRecruiters)",
                           url=(j.get("ref") or f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"))
            if job:
                yield job
        offset += 100
        if offset >= data.get("totalFound", 0):
            break


ATS_DISPATCH = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
                "ashby": fetch_ashby, "smartrecruiters": fetch_smartrecruiters}


# ==========================================================================
# 8. Fonte — Hacker News "Who is hiring" (modo estrito)
# ==========================================================================

def _hn_latest_thread() -> Optional[str]:
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
           "&query=Ask HN: Who is hiring&hitsPerPage=20")
    for h in GET(url).json().get("hits", []):
        t = (h.get("title") or "").lower()
        if "who is hiring" in t and "freelancer" not in t and "wants to be hired" not in t:
            return h.get("objectID")
    return None


def fetch_hackernews(titles, cutoff=None, **_) -> Iterable[Job]:
    tid = _hn_latest_thread()
    if not tid:
        return
    item = GET(f"https://hn.algolia.com/api/v1/items/{tid}").json()
    for c in item.get("children", []):
        text = c.get("text") or ""
        if not text:
            continue
        d = parse_date(c.get("created_at_i"))
        if cutoff and d and d < cutoff:
            continue
        header = clean(text.split("<p>")[0])
        body = clean(text)[:600]
        low = (header + " " + body).lower()
        # pula posts de CANDIDATOS (gente que se posta na thread errada)
        if (re.search(r"willing to relocate|seeking work|seeking employment|"
                      r"open to work|looking for work|wants? to be hired", low)
                or header.lower().startswith(("location:", "remote:", "seeking"))):
            continue
        if not is_software_role(header + " " + body, titles):
            continue
        status = classify(header, header + " " + body)
        if status == "excluir":
            continue
        if status != "incluir" and "remote" not in low:
            continue  # estrito: sem sinal de remoto, fora
        segs = [s.strip() for s in header.split("|") if s.strip()]
        empresa = segs[0][:60] if segs else "Empresa (HN)"
        cargo = next((s for s in segs[1:] if is_software_role(s, titles)),
                     "Engenharia de Software (ver descrição)")
        # link de candidatura no texto, senão o permalink do comentário
        m = re.search(r"https?://[^\s\"'<>)]+", text)
        url = c.get("url") or (m.group(0) if m else f"https://news.ycombinator.com/item?id={c.get('id')}")
        job = make_job(empresa, cargo[:120], location_text=" | ".join(segs[1:4]),
                       date=c.get("created_at_i"), fonte="HN Who is hiring",
                       url=url, extra_text=body, strict=False)
        # força a política estrita do HN (incluir OU verificar-com-remoto)
        if job and (job.status_localizacao == "incluida" or "remote" in low):
            yield job


# ==========================================================================
# 9. Coleta (paralela) + escrita
# ==========================================================================

FIELDNAMES = ["empresa", "cargo", "senioridade", "modelo", "salario", "moeda",
              "salario_estimado", "data_publicacao", "pais", "fonte", "url",
              "status_localizacao", "id"]

# Ordem de prioridade para deduplicação entre fontes (menor = melhor sinal).
SOURCE_PRIORITY = {
    "Gupy": 0, "Programathor": 0, "RemoteOK": 3, "Remotive": 4, "Himalayas": 5,
    "Jobicy": 6, "WeWorkRemotely": 7, "HN Who is hiring": 8, "Arbeitnow": 9,
    "The Muse": 9,
}


def _priority(fonte: str) -> int:
    """Menor = melhor sinal; ao deduplicar, mantém-se a cópia de menor prioridade."""
    if any(k in fonte for k in ("Greenhouse", "Lever", "Ashby", "SmartRecruiters")):
        return 1  # ATS direto = link e dados na fonte
    if fonte.startswith("GitHub"):
        return 2
    return SOURCE_PRIORITY.get(fonte, 5)


def dedup_key(empresa: str, cargo: str) -> Optional[str]:
    """Chave (empresa, cargo) só quando há empresa — senão dois empregadores
    diferentes com o mesmo cargo (vagas sem empresa) colapsariam por engano."""
    if not (empresa or "").strip():
        return None
    return norm_key(empresa, cargo)


def collect(config, since_days) -> list[Job]:
    titles = [s.lower() for s in config.get("software_titles", [])]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    src = config.get("sources", {})

    runners = []  # (label, callable -> Iterable[Job])
    board_map = {
        "remoteok": ("RemoteOK", fetch_remoteok), "remotive": ("Remotive", fetch_remotive),
        "weworkremotely": ("WeWorkRemotely", fetch_wwr), "himalayas": ("Himalayas", fetch_himalayas),
        "jobicy": ("Jobicy", fetch_jobicy), "arbeitnow": ("Arbeitnow", fetch_arbeitnow),
        "themuse": ("The Muse", fetch_themuse), "gupy": ("Gupy", fetch_gupy),
        "programathor": ("Programathor", fetch_programathor),
        "githubvagas": ("GitHub vagas BR", fetch_github_boards),
        "hackernews": ("HN Who is hiring", fetch_hackernews),
    }
    for key, (label, fn) in board_map.items():
        if src.get(key):
            runners.append((label, lambda fn=fn: fn(titles, cutoff=cutoff)))
    for c in config.get("ats_companies", []):
        fn = ATS_DISPATCH.get((c.get("ats") or "").lower())
        if fn:
            runners.append((c["name"],
                            lambda fn=fn, c=c: fn(c["name"], c["slug"], titles, cutoff=cutoff)))

    jobs: list[Job] = []
    log_lock_msgs = []

    def run_one(label, runner):
        out, n = [], 0
        try:
            for job in runner():
                d = job._dt
                if d is not None and d < cutoff:
                    continue
                out.append(job)
                n += 1
            return label, out, f"  [ok] {label}: {n} vaga(s)"
        except Exception as exc:
            return label, out, f"  [WARN] {label} falhou: {str(exc)[:80]}"

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(run_one, label, runner) for label, runner in runners]
        for f in cf.as_completed(futs):
            label, out, msg = f.result()
            jobs.extend(out)
            print(msg, file=(sys.stderr if "[WARN]" in msg else sys.stdout))
    return jobs


def write_csv(jobs: list[Job], out: Path) -> tuple[int, int]:
    """Funde com o CSV existente, deduplica por id e por (empresa,cargo)."""
    rows = []
    seen_ids, seen_keys = set(), set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)
                seen_ids.add(row.get("id"))
                k = dedup_key(row.get("empresa", ""), row.get("cargo", ""))
                if k:
                    seen_keys.add(k)

    # novas vagas ordenadas por prioridade de fonte: diretas (ATS/Gupy) primeiro,
    # para que suas chaves populem antes — aí os agregadores dedupam contra elas.
    new_sorted = sorted(jobs, key=lambda j: _priority(j.fonte))
    added = 0
    for j in new_sorted:
        if j.id in seen_ids:
            continue
        k = dedup_key(j.empresa, j.cargo)
        if k and k in seen_keys:
            continue  # mesma empresa+cargo (cross-fonte ou mass-poster) -> 1 só
        rows.append({f: getattr(j, f) for f in FIELDNAMES})
        seen_ids.add(j.id)
        if k:
            seen_keys.add(k)
        added += 1

    rows.sort(key=lambda r: (r.get("data_publicacao") or "", r.get("empresa") or ""),
              reverse=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    return added, len(rows)


def main():
    ap = argparse.ArgumentParser(description="Agregador de vagas de SWE para brasileiros")
    ap.add_argument("--since-days", type=int, default=1,
                    help="janela em dias (7 na primeira passagem, 1 no diário)")
    ap.add_argument("--out", default="vagas.csv")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    t0 = time.time()
    print(f"Coletando vagas dos últimos {args.since_days} dia(s)...")
    jobs = collect(config, args.since_days)
    added, total = write_csv(jobs, Path(args.out))

    inc = sum(1 for j in jobs if j.status_localizacao == "incluida")
    ver = sum(1 for j in jobs if j.status_localizacao == "verificar")
    brl = sum(1 for j in jobs if j.moeda == "BRL")
    usd = sum(1 for j in jobs if j.moeda == "USD")
    print(f"\nResumo ({time.time()-t0:.0f}s): {len(jobs)} coletadas "
          f"| {inc} incluídas, {ver} a verificar | {brl} em R$, {usd} em US$ "
          f"| {added} novas (total {total}) em {args.out}")


if __name__ == "__main__":
    main()
