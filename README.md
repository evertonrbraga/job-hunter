# vaga-gringa-bot 🇧🇷→🌍 (v2)

Agregador de vagas de **engenharia de software abertas a brasileiros**, varrendo o
máximo de fontes públicas possível. Pega **vagas em US$ (mundo todo)** e **vagas em
R$ (Brasil)**, filtra por elegibilidade geográfica e acumula tudo numa planilha.

Roda local (dois scripts) ou sozinho todo dia (GitHub Actions).

---

## O que ele varre

**Boards internacionais (vagas em US$, do mundo todo):**
- **RemoteOK**, **Remotive** (várias categorias), **We Work Remotely** (vários feeds),
  **Himalayas** (~86 mil vagas, com restrição de localização *estruturada* + moeda),
  **Jobicy** (filtro por geografia: Brazil / LatAm / Anywhere),
  **Arbeitnow** e **The Muse** (globais → modo estrito, só worldwide explícito).

**Brasil (vagas em R$):**
- **Gupy** (o maior ATS do Brasil — remoto + híbrido), **Programathor**.

**Direto na fonte — ATS de ~75 empresas-alvo** (todas validadas em jun/2026):
- **Greenhouse, Lever, Ashby, SmartRecruiters** (Stripe, Databricks, OpenAI, Notion,
  Remote.com, QuintoAndar, Spotify, Canonical, GitLab, Mozilla, Cloudflare…).

**Comunidade:**
- **Hacker News “Who is hiring”** — a thread mensal, via API do Algolia
  (modo estrito + filtro que ignora posts de candidatos).

**NÃO cobre, de propósito:** LinkedIn, Indeed, Wellfound, Glassdoor. São muralhas
anti-bot; automatizar viola os Termos e arrisca banir sua conta. Para esses: manual.

---

## Como ele decide se a vaga aceita brasileiro

Cada vaga é classificada por **localização**:

| status | significa | vai p/ planilha? |
|---|---|---|
| `incluida` | diz worldwide/anywhere/global/**LatAm/Brazil**, OU é fonte BR, OU a restrição estruturada inclui o Brasil | ✅ sim |
| `verificar` | só diz “Remote” genérico, sem dizer a região → você checa à mão | ✅ sim (sinalizada) |
| *(excluída)* | exige autorização nos EUA / “US only” / “no visa sponsorship” / travada em EMEA/EU/UK/APAC/Canadá/Índia, ou lista só países sem o Brasil | ❌ **descartada** |

Detalhes que evitam falso-positivo:
- A **inclusão** olha só título+localização (curto e confiável); a **descrição
  longa** só serve para detectar *exclusão* (assim “global teams” no meio de um texto
  de vaga US-only não inclui a vaga por engano).
- **Menção explícita ao Brasil/LatAm no título vence** uma restrição estruturada
  errada (ex.: vaga “Remote in Brazil” que o board marcou como “United States”).

> Validado no run real: **0 vagas US/UK-only vazaram** para `incluida`.

---

## A planilha (`vagas.csv`)

CSV acumulado e deduplicado, com as colunas:

| coluna | descrição |
|---|---|
| `empresa` | nome da empresa |
| `cargo` | título da vaga |
| `senioridade` | Júnior / Pleno / Sênior / Liderança/Staff (derivada do título) |
| `modelo` | Remoto / Híbrido / Presencial |
| `salario` | valor publicado, ou estimativa por senioridade |
| `moeda` | `USD`, `BRL`, `EUR`… |
| `salario_estimado` | `TRUE` = veio da estimativa, não da vaga |
| `data_publicacao` | AAAA-MM-DD |
| `pais` | texto de localização da vaga |
| `fonte` | board/ATS de origem |
| `url` | link direto para a vaga |
| `status_localizacao` | `incluida` ou `verificar` |
| `id` | hash p/ deduplicação |

**Deduplicação:** por `id` (mesma URL) e, entre *agregadores*, por empresa+cargo
(a mesma vaga aparece em vários boards). Vagas diretas de ATS/Gupy mantêm cada URL
distinta. Rodar de novo **não duplica** — só acrescenta o que é novo.

**Salário:** quando a vaga publica, usa o valor real. Quando não, **estima por
senioridade** (US$/ano p/ vagas gringas; R$/mês p/ vagas BR) e marca
`salario_estimado = TRUE`. Trate como chute fundamentado.

### Ver como Google Sheet ao vivo (sem API)

Numa célula do Sheets, depois que o CSV estiver no GitHub:

```
=IMPORTDATA("https://raw.githubusercontent.com/evertonrbraga/job-hunter/main/vagas.csv")
```

---

## Uso

### Localmente (dois scripts prontos)

```bash
./buscar_7dias.sh    # vagas dos ÚLTIMOS 7 DIAS  (primeira passagem)
./buscar_24h.sh      # vagas das ÚLTIMAS 24 HORAS (atualização incremental)
```

Na primeira execução eles criam o `.venv` e instalam as dependências sozinhos.
Também dá para chamar direto:

```bash
python job_hunter.py --since-days 7   # ou 1, 14, 30...
```

### Automático todo dia (GitHub Actions)

O workflow `.github/workflows/daily.yml` roda **todo dia às 09:00 UTC = 06:00 de
São Paulo**, busca as **últimas 24h** e dá `commit` no `vagas.csv` atualizado.
Para testar agora: GitHub → aba **Actions** → *vaga-gringa-daily* → **Run workflow**.

> O agendamento do Actions é desativado após ~60 dias sem atividade no repo;
> qualquer commit (ou um “Run workflow” manual) reativa.

---

## Como estender

- **Mais empresas-alvo:** adicione linhas em `ats_companies` no `config.yaml`. Abra a
  página de carreiras e pegue o slug da URL:
  `jobs.ashbyhq.com/SLUG` · `boards.greenhouse.io/SLUG` · `jobs.lever.co/SLUG` ·
  SmartRecruiters `careers/SLUG`. Se o slug logar `[WARN]`, está errado.
- **Mais palavras de cargo:** edite `software_titles` (já cobre PT + EN).
- **Mais/menos fontes:** ligue/desligue em `sources:` no `config.yaml`.

---

## Aviso

Ferramenta de **uso pessoal**, consumindo APIs/feeds públicos. Respeite os Termos de
cada serviço e não rode em frequência abusiva (1x/dia está ótimo).
