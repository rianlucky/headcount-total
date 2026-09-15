# Contexto do produto

## Objetivo

Ser um guia público para qualquer pessoa consultar total de headcount, alocação de mão de obra e quantidade de colaboradores em uma data ou período específico.

## Limites de publicação

O painel deve mostrar apenas dados agregados. Não publicar gênero, raça, idade, salários, remuneração, nomes, matrículas ou qualquer outro identificador individual.

## Estado atual

O painel tem integração real funcionando: Databricks → (job semanal) → Neon → Streamlit. Sem essa integração configurada, cai para o mock local sintético/determinístico (snapshots mensais de janeiro de 2024 a agosto de 2026) — esse mock serve só de demonstração, não representa mais a taxonomia real.

## Taxonomia oficial dos agrupadores públicos

Confirmado: os agrupadores públicos de negócio são **Diretoria**, **Área**, **Estado** e **Nível de Cargo**. Com integração real, os valores de Diretoria e Área são os nomes reais da empresa (vindos do Databricks) — deixaram de ser uma lista fixa. O painel calcula as opções de filtro a partir do que realmente veio na carga.

- **Nível de cargo:** os 5 níveis (Direção, Gerência, Coordenação, Especialista, Operacional) continuam sendo o contrato público — mapeados a partir de `funcao_cargo` no Databricks pelo job de extração (ver `etl/extract_databricks_to_neon.py`, tabela de mapeamento no README).
- **Estado:** mapeado de UF (como vem no Databricks) pro nome completo.

Modelo de trabalho foi removido do painel: sem métrica útil para ele por enquanto.

## Contrato técnico (Neon)

O painel lê de uma tabela `headcount_publico` no Neon (Postgres) com `snapshot_date`, `diretoria`, `area`, `job_level`, `state`, `headcount`. Quem alimenta essa tabela é o job semanal em `etl/`, nunca o Streamlit diretamente — ver "Decisão: pipeline via Neon" abaixo e o README pra detalhes técnicos.

## Decisão: pipeline provisório via Neon (2026-09-10/11)

A política da empresa bloqueia tanto a criação de um Service Principal (M2M) quanto a geração de Personal Access Tokens no Databricks para o usuário atual ("Tokens are disabled for your organization"). Testamos originalmente usar o Microsoft Fabric como camada intermediária, mas o workspace disponível é só Power BI Pro (sem capacidade Fabric contratada) — sem viabilidade imediata. Pivotamos para o **Neon** (Postgres serverless), que já existia como conta de teste do usuário (agora um projeto dedicado, conta corporativa).

Pipeline final:

1. **Leitura do Databricks:** nem PAT nem M2M funcionam, mas o login interativo (**OAuth user-to-machine**, `auth_type="databricks-oauth"` no `databricks-sql-connector`) funciona — usa a mesma conta que já acessa o workspace pela UI, sem precisar de nenhuma credencial administrada. É a base de leitura do job de extração.
2. **Job semanal** (`etl/extract_databricks_to_neon.py`, Windows Task Scheduler local — usuário assume a automação do login): lê `rh.gold.fato_funcionario_ativo` já agregado (`GROUP BY` na query, nenhuma linha individual sai do warehouse), mapeia `funcao_cargo`→nível de cargo e UF→estado, e grava no Neon via upsert.
3. **Painel lê só do Neon.** Nunca do Databricks diretamente.

Achados importantes durante a implementação:

- `rh.gold.fato_funcionario_ativo` só guarda o **estado atual** (uma carga, sem histórico) — por isso o Neon acumula um snapshot novo por execução do job, em vez de substituir a tabela inteira. Não há como reconstruir histórico anterior a hoje a partir do gold; o bronze (`rh.bronze.oracle_hcm_pit_*`) tem ~109 cargas diárias desde 2026-05-24, mas é dado bruto (colunas em português com encoding cru, sem limpeza) — não usado por ora.
- Diretorias reais da empresa (14, ex.: Diretoria de Obras 1/2, Diretoria Comercial, CAPEX, Conselho Administrativo, Presidência) substituíram a taxonomia mock (Construção/Engenharia/Operações/Comercial/Corporativo), que era só placeholder.
- **Decisão de privacidade aceita pelo usuário:** algumas diretorias são pequenas (ex.: "Diretoria de Operações" tem 2 pessoas) — risco teórico de reidentificação num painel público. Decisão: publicar os números exatos mesmo assim por enquanto, provisório. Revisar se o painel ganhar mais visibilidade/audiência.
- O painel **continua público**, sem login.
- Isso é reconhecidamente provisório: quando existir uma plataforma de dados gerenciável de forma mais livre, este pipeline (script + agendamento local) é substituído por ela.

## Decisões de produto

- A data é tratada como snapshot mensal, não como evento diário.
- Filtros globais ficam na lateral.
- A página prioriza leitura rápida com KPIs, tendência e composição.
- O resumo exportável também é agregado.
- A linguagem é simples e pública, sem jargão de RH desnecessário.
- Área, estado e nível de cargo entram como dimensões agregadas, sujeitos a revisão de risco de reidentificação antes da publicação.
- Escopo restrito a headcount: **não** exibir admissões, desligamentos ou saldo por enquanto.
- Por ora, só o mockup evolui com a taxonomia oficial; a integração real com Databricks (estado, frequência de publicação) entra depois.
- Crescimento (1/3/12 meses) e "quem mais mudou" usam só aritmética de headcount sobre o histórico completo — não contam como admissão/desligamento/saldo, então ficam dentro do escopo atual. Revisado a partir do dashboard interno de RH (`C:\Dev\Projetos\StreamlitRH\pages\1_Headcount.py`), mas sem trazer granularidade por colaborador, gênero, tempo de casa ou família de cargo — nada disso é público.

## Perguntas em aberto

- **Agendamento do job semanal:** ainda não configurado no Windows Task Scheduler — usuário vai automatizar o login OAuth do Databricks por conta própria antes de agendar.
- Se/quando o painel ganhar mais audiência, revisar a decisão de publicar diretorias pequenas com número exato (risco de reidentificação, aceito como provisório).
- **Governança de acesso ao schema `interno` do Neon (ver seção abaixo):** views mascaradas, roles com grant restrito etc. — próximo passo, fora do escopo deste painel público.

## Decisão: mirror interno de PII no Neon, schema `interno` (2026-09-11)

Depois de resolver o CAPEX/Presidência (abaixo) e reconstruir histórico real via bronze, o usuário decidiu parar de disputar acesso analítico direto no Databricks a cada nova necessidade e, em vez disso, trazer as tabelas fato inteiras pro Neon — inclusive dados individuais de colaboradores — e tratar governança de acesso dentro do próprio Neon dali pra frente.

- **O quê:** `rh.gold.fato_funcionario`, `fato_funcionario_ativo`, `fato_funcionario_inativo`, `fato_funcionario_evol_cargos` e `fato_movimentacao` espelhadas por inteiro via `etl/mirror_fatos_to_neon.py` — truncate + reload completo a cada execução.
- **Isolamento:** schema `interno`, separado do schema público que o painel usa. **`app.py` nunca lê esse schema** — o painel continua só em `public.headcount_publico` (agregado).
- Governança de acesso a esse schema interno (views mascaradas, roles com grant restrito etc.) é tratada fora do escopo deste painel público.
- Isso muda o modelo de governança que valia até aqui ("nenhuma linha individual sai do Databricks") — agora vale só pro caminho público (`headcount_publico`), não mais pro projeto como um todo.

## Correção de mapeamento CC→diretoria por cargo/pessoa (2026-09-11)

Usuário percebeu que "CAPEX" e "Presidencia" (diretorias no painel) misturavam gente de várias diretorias reais, porque alguns CCs são "guarda-chuva" administrativo (ex.: todo mundo com CC 47700 "Presidência Executiva" caía em "Presidencia", mas na real são 10 diretores de outras áreas + presidente + motorista). Resolvido com `etl/special_mappings.json`: resolve por título de cargo dentro do CC (`by_position`) e, quando dois titulares têm o cargo idêntico mas vão pra diretorias diferentes (ex.: dois "Diretor de Negócios", um pra Negócios 1 e outro pra Negócios 2), por `id_funcionario` (`by_person` — nunca por nome). Lógica compartilhada em `etl/mapping.py` entre o job semanal e o backfill, pra histórico e presente não divergirem.

## Histórico com quebra real, pessoa a pessoa, para os 24 meses inteiros (2026-09-11)

Depois do mirror de PII no Neon (acima), veio à tona `interno.fato_funcionario_evol_cargos` — dimensão tipo-2 (intervalo de validade `data_de`/`data_ate` de centro de custo/cargo por pessoa) cobrindo desde 2006. Isso permite saber o CC/cargo de **qualquer pessoa em qualquer data histórica**, não só nos meses com snapshot diário do bronze.

`etl/backfill_from_neon_history.py` substitui o backfill anterior (que era total-only fora de maio–agosto/2026): pra cada mês, calcula quem estava ativo (`interno.fato_funcionario`, data_admissao/data_desligamento) e resolve o CC/cargo de cada um no intervalo de `fato_funcionario_evol_cargos` que cobre aquela data, aplicando o mesmo `mapping.py` (CC_MAPPING + SPECIAL_MAPPINGS + JOB_LEVEL_MAP). Roda 100% contra o Neon — não precisa mais consultar o Databricks pra isso.

- **Não existe mais nenhuma linha "Total histórico"** — todos os 24 meses (set/2024 a ago/2026) têm quebra real por diretoria/área/estado/cargo, com "Não informado" absorvendo quem não tem CC mapeado ou não tem intervalo de cargo cobrindo aquela data (~15-20% nos meses mais antigos, ~6% nos mais recentes — melhora com o tempo porque `fato_funcionario_evol_cargos` tem mais cobertura pra gente admitida há mais tempo).
- **Limitações aceitas:** `estado` usa o valor mais recente conhecido da pessoa (não há rastro de mudança de estado); `job_level` é aproximado pela primeira palavra de `descricao_cargo` (ex.: "Diretor de Negócios" → "Diretor"), já que essa tabela não guarda `funcao_cargo` diretamente.
- Decisão do usuário: "para o dashboard vamos apenas colocar os dados que já utilizamos" — a saída pro painel público continua exatamente as mesmas 4 dimensões (diretoria/área/estado/nível de cargo), só ficou mais precisa por trás.
- `etl/backfill_headcount_history.py` (a versão anterior, total-only + bronze PIT) foi removido — substituído por este.
