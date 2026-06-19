# vaga-gringa-bot 🇧🇷→🌍

Agregador de vagas de **engenharia de software remotas abertas a brasileiros**.
Roda sozinho todo dia, filtra por elegibilidade geográfica e acumula tudo numa planilha.

---

## ⚠️ Leia primeiro: o que ele faz e o que NÃO faz (sem ilusão)

**Cobre com solidez** (fontes com API/RSS pública, estáveis e dentro dos termos):
- **RemoteOK, Remotive, We Work Remotely** — boards remotos com API/feed.
- **Greenhouse / Lever / Ashby** — as APIs públicas de vagas das **suas
  empresas-alvo** (a parte de maior sinal: vaga direto na fonte).

**NÃO cobre, de propósito:**
- **LinkedIn, Indeed, Wellfound, Glassdoor.** São muralhas anti-bot. Automatizar
  navegação logada nesses (principalmente o LinkedIn) **viola os Termos de Uso e
  tem risco real de banir sua conta**, além de quebrar a cada mudança de layout.
  Para esses, continue no manual + alertas por e-mail/RSS.

**Sobre o passo "checar People do LinkedIn":** o script **não loga nem scrapeia o
LinkedIn**. As vagas sem localização especificada entram na planilha com status
`verificar` — aí você faz a checagem manual (do jeito da screenshot: People →
filtro "Brazil"/"Brasil", leva segundos) ou olha a página de carreiras da
empresa, que costuma ser mais explícita. É o caminho seguro.

**Salário:** quando a vaga publica, usa o valor real. Quando não publica, **estima
por senioridade** (faixas de mediana de mercado, contexto contractor LatAm) e
marca a linha com `salario_estimado = TRUE`. Trate como chute fundamentado.

---

## A planilha

CSV (`vagas.csv`) acumulado, com as colunas:

| coluna | descrição |
|---|---|
| `empresa` | nome da empresa |
| `cargo` | título da vaga |
| `salario` | valor publicado, ou estimativa (~US$X–Yk) |
| `salario_estimado` | `TRUE` = veio da estimativa, não da vaga |
| `data_publicacao` | AAAA-MM-DD |
| `pais` | texto bruto de localização da vaga |
| `fonte` | board/ATS de origem |
| `url` | link direto para a vaga |
| `status_localizacao` | `incluida` (diz worldwide/latam/brazil/...) ou `verificar` (não diz nada — checar à mão) |
| `id` | hash p/ deduplicação |

> Vagas claramente fechadas (exigem autorização nos EUA, "US only", "no visa
> sponsorship", ou travadas em EMEA/EU/UK/APAC) são **descartadas** e não entram.

### Quer ver como Google Sheet ao vivo, sem API?

Depois que o CSV estiver no GitHub, numa célula do Sheets:

```
=IMPORTDATA("https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPO/main/vagas.csv")
```

O Sheets relê o arquivo periodicamente — planilha viva, zero configuração de API.

---

## Setup

```bash
# 1. criar repo e jogar estes arquivos dentro
git init && git add . && git commit -m "vaga-gringa-bot"

# 2. ambiente local
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. editar config.yaml -> adicionar suas empresas-alvo (slugs de ATS)
#    e ajustar software_titles se quiser
```

### Primeira passagem (última semana)

```bash
python job_hunter.py --since-days 7
```

Isso cria/preenche `vagas.csv` com as vagas dos últimos 7 dias.

### Daí em diante: diário automático às 6h

Suba o repo para o GitHub. O workflow `.github/workflows/daily.yml` roda **todo dia
às 09:00 UTC = 06:00 de São Paulo**, busca as **últimas 24h** e dá `commit` no
`vagas.csv` atualizado (cumulativo, sem duplicar).

Para testar agora: GitHub → aba **Actions** → *vaga-gringa-daily* → **Run workflow**.

---

## Por que GitHub Actions (e não o Cowork)?

Você perguntou se o Cowork seria melhor. Resposta honesta: **não, para isto.**

- **Cowork** é um app **agêntico interativo** de desktop — excelente para "vá
  pesquisar/fazer X agora". Mas ele **não roda sozinho às 6h** com a máquina
  fechada; depende de você e da sessão aberta. Não é um agendador.
- **GitHub Actions** é **cron na nuvem**: roda no horário marcado mesmo com seu
  computador desligado, é **grátis** para reppositório público (e tem cota
  generosa no privado) e encaixa no seu fluxo (você já usa GitHub/Claude Code).

Alternativas válidas: `cron`/`launchd` (Mac) ou Task Scheduler (Windows) — mas só
rodam com a máquina ligada; ou um VPS baratinho / Raspberry Pi sempre-ligado.
Actions é o melhor custo-benefício.

**Ressalvas honestas do Actions:** workflows agendados podem **atrasar alguns
minutos** em horários de pico, e o GitHub **desativa o agendamento após ~60 dias
sem atividade** no repo. Rodar o "Run workflow" de vez em quando (ou qualquer
commit) mantém vivo.

---

## Como estender

- **Mais empresas-alvo:** é só adicionar linhas em `ats_companies` no `config.yaml`.
- **Mais boards com API:** `ai-jobs.net` e o `Himalayas` expõem API — dá para criar
  novas funções `fetch_*` no mesmo padrão das existentes.
- **Hacker News "Who is Hiring":** dá para puxar a thread mensal via API do HN
  (Algolia) e filtrar por "LATAM/Brazil/Worldwide". Fica para a v2 (o texto é
  livre e exige parsing mais sujo).
- **Google Sheets via API (em vez de IMPORTDATA):** posso adicionar escrita direta
  com `gspread` + service account, se você preferir. É só pedir.

---

## Aviso

Ferramenta para **uso pessoal** de busca de vagas, consumindo APIs/feeds públicos.
Respeite os Termos de Uso de cada serviço e não rode em frequência abusiva
(1x/dia está ótimo).
