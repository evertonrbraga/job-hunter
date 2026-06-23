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
- **Gupy** (o maior ATS do Brasil — remoto + híbrido), **Programathor**, e os
  **boards de vagas no GitHub Issues** (frontendbr, backend-br, react-brasil,
  golang-brasil… — API REST oficial, 9 boards validados).

**LinkedIn (sem login):**
- usa o endpoint **público “guest”** de busca de vagas (`jobs-guest/...`) — o mesmo
  que ferramentas open-source usam. **Não loga nem scrapeia área autenticada.** É
  *best-effort*: o LinkedIn limita por IP, então às vezes vem menos (a coleta só
  segue em frente). Busca remoto em Brasil / LatAm / Worldwide.

**Direto na fonte — ATS de ~80 empresas-alvo** (todas validadas em jun/2026):
- **Greenhouse, Lever, Ashby, SmartRecruiters** (Stripe, Databricks, OpenAI, Notion,
  Remote.com, QuintoAndar, Spotify, Canonical, GitLab, Cloudflare…), incluindo
  **empresas do Y Combinator** (ElevenLabs, Decagon, Fivetran, Checkr, Mintlify…).

**Comunidade:**
- **Hacker News “Who is hiring”** — a thread mensal, via API do Algolia (modo
  estrito + filtro que ignora posts de candidatos). É **fortíssima em startups YC**.

**NÃO cobre (testado e comprovadamente inviável sem burlar):**
- **Wellfound** (ex-AngelList) e **Indeed/Glassdoor**: muralha Cloudflare que
  bloqueia até Chromium real *headed* (testei: HTTP, headless, headed, sitemap e
  até UA de Googlebot → tudo `403`). Só passaria com proxy residencial pago, o que
  viola os Termos e quebra direto. O próprio Wellfound convida ao **API parceiro**.
- **Y Combinator “Work at a Startup”**: a página é só JS (sem dados no HTML) atrás
  de Cloudflare. Por isso o YC entra pela via confiável: ATS das empresas + HN.

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
| `email` | e-mail de candidatura, quando o anúncio traz (HN e boards do GitHub) |
| `status_localizacao` | `incluida` ou `verificar` |
| `id` | hash p/ deduplicação |

**Deduplicação:** por `id` (mesma URL) e por **empresa+cargo** quando há empresa
(mata a mesma vaga repetida entre fontes e o spam de *body shops* que postam o
mesmo cargo 7×). Vagas sem empresa (ex.: HN) só deduplicam por URL. Rodar de novo
**não duplica** — só acrescenta o que é novo.

**Salário:** quando a vaga publica, usa o valor real. Quando não, **estima por
senioridade** (US$/ano p/ vagas gringas; R$/mês p/ vagas BR) e marca
`salario_estimado = TRUE`. Trate como chute fundamentado.

---

## Google Sheets (escrever direto no Drive a cada execução)

O script **sobrescreve** a aba da sua planilha com o `vagas.csv` completo a cada
run. Já está configurado em `config.yaml` (`google_sheets:`) apontando para a sua
planilha. Falta só a **credencial** (uma vez, ~3 min):

1. Acesse <https://console.cloud.google.com> → crie um projeto (ou use um).
2. **APIs & Services → Enable APIs** → ative **Google Sheets API**.
3. **Credentials → Create credentials → Service account** → dê um nome → Create.
4. No service account → aba **Keys → Add key → JSON** → baixa um arquivo `.json`.
5. Salve esse arquivo como **`credentials.json`** na pasta do projeto
   (já está no `.gitignore` — **nunca** suba ao GitHub).
6. Abra o `.json`, copie o `client_email`
   (algo como `vaga-bot@projeto.iam.gserviceaccount.com`).
7. Abra sua planilha → **Compartilhar** → cole esse e-mail → permissão **Editor**.

Pronto. Rode `./buscar_7dias.sh` e a planilha é preenchida sozinha.
Para desligar numa rodada: `python job_hunter.py --since-days 7 --no-sheets`.

**No GitHub Actions (diário):** repo → **Settings → Secrets and variables →
Actions → New repository secret** → nome `GOOGLE_CREDENTIALS_JSON` → cole **todo o
conteúdo** do `credentials.json`. O `daily.yml` já repassa esse secret.

> **Alternativa sem credencial (IMPORTDATA):** numa célula do Sheets, com o repo
> público, `=IMPORTDATA("https://raw.githubusercontent.com/evertonrbraga/job-hunter/main/vagas.csv")`.
> A planilha relê o CSV sozinha — mas só atualiza quando o CSV é enviado ao GitHub.

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

**Filtrar por papel e/ou por candidatura via e-mail:**

```bash
# só vagas de frontend que dá pra aplicar mandando e-mail (nicho pequeno -> janela maior)
python job_hunter.py --since-days 90 --role frontend --only-email --out vagas_frontend_email.csv --no-sheets
```

`--role` aceita `frontend`, `backend`, `mobile`, `devops`, `data` (ou qualquer
palavra). `--only-email` mantém só anúncios com e-mail de candidatura (na prática,
**Hacker News** e os **boards do GitHub** — ATS/agregadores aplicam por formulário).

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
