# Changelog

## [Não lançado]

- **Integração à Central de Gente & Dados no Neon — migração 002 (2026-09-24):** tabelas reorganizadas por domínio (ver `00 - Central de Gente & Dados/PLANO_INTEGRACAO_NEON.md` e `_neon/migrations/002_headcount_total.sql`). `interno.fato_*` → **`core.fato_*`** (fonte da verdade compartilhada, com PII) e `headcount_publico`/`headcount_extra_metrics` → **`org.*`**. Nos nomes antigos ficaram views de compatibilidade, pra Movimentações/Aderência/Estudos Salariais continuarem lendo até migrarem. Código passa a qualificar os nomes (`org.headcount_publico`, `core.fato_funcionario` etc.). Usuários dedicados: painel conecta como **`app_headcount`** (só leitura de `org.headcount_*` + login) e os scripts de `etl/` como **`etl_loader`** — o `neondb_owner` sai do dia a dia. `auth.init_db()` só cria `app_users` se ela não existir (o usuário do app não tem permissão de CREATE). `mirror_fatos_to_neon.py` não cria mais schema (é papel das migrações).
- **Histórico ganha quebra real (pessoa a pessoa) pros 24 meses inteiros, sem exceção (2026-09-11):** `interno.fato_funcionario_evol_cargos` (mirrorada no passo anterior) é uma dimensão tipo-2 de CC/cargo por pessoa cobrindo desde 2006 — dá pra saber a diretoria/área de qualquer pessoa em qualquer mês histórico. Novo `etl/backfill_from_neon_history.py` substitui o backfill anterior (total-only fora de maio–ago/2026): não existe mais nenhuma linha "Total histórico", todo o período (set/2024–ago/2026) tem quebra real por diretoria/área/estado/cargo, rodando 100% contra o Neon (sem consultar o Databricks de novo). `etl/backfill_headcount_history.py` removido.
- **Mirror interno de dados de colaboradores no Neon (schema `interno`, 2026-09-11):** decisão do usuário de parar de disputar acesso analítico direto no Databricks e trazer as tabelas fato inteiras (`fato_funcionario`, `fato_funcionario_ativo`, `fato_funcionario_inativo`, `fato_funcionario_evol_cargos`, `fato_movimentacao`) pro Neon. Novo script `etl/mirror_fatos_to_neon.py` (truncate + reload completo a cada execução). **Isolamento:** schema `interno`, nunca lido por `app.py` — o painel público continua só em `headcount_publico` (agregado). Governança de acesso dentro do Neon fica como próximo passo, fora do escopo deste painel.
- **Correção pontual de mapeamento CC→diretoria por cargo/pessoa (2026-09-11):** CCs "guarda-chuva" como 47700 (Presidência Executiva) e 47276 (CAPEX) concentravam gente de várias diretorias reais. Novo `etl/special_mappings.json` resolve por título de cargo (`by_position`) e, quando dois titulares têm o mesmo cargo mas vão pra diretorias diferentes, por `id_funcionario` (`by_person`) — nunca nome. Lógica de resolução (CC_MAPPING + SPECIAL_MAPPINGS + níveis de cargo/estado) extraída pra `etl/mapping.py`, compartilhada entre o job semanal e o backfill.
- **Histórico com quebra real por diretoria/área/estado/cargo pra maio–agosto/2026:** achado `rh.bronze.oracle_hcm_pit_adm_00005_funcs_ativos_relatorio` — snapshot diário completo desde 24/05/2026 (109 dias), com centro de custo/cargo/estado por pessoa. `etl/backfill_headcount_history.py` reescrito: meses com PIT disponível (mai–ago/2026) usam quebra real; meses antes disso continuam só "Total histórico" (sem PIT, sem gold com histórico dimensional). Seção "Quem mais mudou" passa a ter dado real pra comparar, não só a partir do snapshot semanal atual.
- **OAuth do Databricks para de abrir aba a cada execução:** `experimental_oauth_persistence=DevOnlyFilePersistence(...)` (recurso beta do `databricks-sql-connector`) persiste o refresh token em `etl/.oauth_token_cache.json` (nunca commitado) — login por navegador só quando o token expira de verdade, não mais a cada `python etl/*.py`.
- Removido "Não informado" da composição por diretoria/área/estado/cargo — CCs sem mapeamento não aparecem mais como se fossem uma diretoria real.
- Adicionado badge "🔄 Atualizado em: DD/MM/AAAA" ao lado de "Dados públicos", com a data do snapshot mais recente.
- **"Centro de custo" trocado por "Área" + correção de diretoria via mapeamento manual:** a dimensão `cost_center` (código bruto do CC, ~209 valores, ilegível em gráfico e com sujeira de dado — ex.: `52609.`) foi substituída por `area`, usando um mapeamento manual CC→Diretoria→Área fornecido pelo usuário em 2026-09-11 (`etl/cc_mapping.json`, 546 CCs, cobre 100% dos CCs em uso — conferido). Esse mapeamento também **corrige diretoria**: 10 CCs que o `gold` do Databricks ainda atribui a "Diretoria de Planejamento" já são "Diretoria de Engenharia Técnica" de verdade (reorganização que o join automático do gold não capturou). Coluna `cost_center` renomeada para `area` no Neon.
- **Correção de bugs críticos de UX, encontrados com revisão visual real (Playwright + Chromium headless, instalado nesta sessão):**
  - Sidebar forçada em `initial_sidebar_state="expanded"` quebrava o layout no celular (ficava aberta por cima do conteúdo em vez de recolher). Trocado pra `"auto"`.
  - Barra de dev do Streamlit ("Deploy"/"Stop") escondida via `client.toolbarMode = "viewer"` — não faz sentido num link público.
  - Gráficos de composição (Diretoria/Área/Estado/Nível de cargo) viraram barras horizontais (antes truncavam nomes longos tipo "Diretoria Adm. Fi..."), com `labelLimit` maior. "Área" (67 valores) ganhou agrupamento Top 10 + "Outros".
- **Identidade visual alinhada aos outros dashboards da Pacaembu:** primeira tentativa usou a paleta do prompt do Genie (`#064D66` + amarelo + Inter), mas nenhum dashboard real usa isso. Corrigido pra paleta confirmada no RealizaDO (`Remuneração e Orçamento de Pessoas/RealizaDO`, o mais recente/deliberado dos dashboards internos): navy institucional `#003244`, verde `#0ca30c`, vermelho `#d03b3b`, azul `#2a78d6`, laranja `#eb6834`, fonte padrão do sistema (sem Google Fonts). Tudo via `.streamlit/config.toml`.
- Adicionado CSS customizado nos cards de KPI e composição (fundo branco, sombra, borda de destaque navy à esquerda) — pedido explícito do usuário, inspirado no `StreamlitRH/9_Remuneração Total.py`. Cores dos elementos CSS também corrigidas pra paleta confirmada (nada de dourado não confirmado).
- Reconstruído o histórico de headcount total (24 meses, set/2024 a ago/2026) a partir de `data_admissao`/`data_desligamento` no Databricks — sem depender de snapshots periódicos que não existem. Decisão: só o total geral tem esse histórico; a quebra por diretoria/centro de custo/estado/cargo só existe a partir do snapshot semanal mais recente (posição histórica de cada pessoa não é rastreável com confiança). `app.py` reestruturado pra separar "tendência" (sempre total da empresa) de "composição" (sempre o snapshot mais recente).
- **Pivô de Fabric para Neon:** o workspace do Fabric disponível não tinha capacidade contratada (só Power BI Pro). Trocado o destino do pipeline provisório para o Neon (Postgres), num projeto dedicado. Removido `etl/extract_databricks_to_fabric.py`.
- Adicionado `etl/extract_databricks_to_neon.py`: lê `rh.gold.fato_funcionario_ativo` no Databricks (agregado por `GROUP BY`, autenticando via OAuth user-to-machine — nem PAT nem M2M estavam disponíveis), mapeia `funcao_cargo`→nível de cargo e UF→estado, e grava no Neon via upsert (acumula um snapshot novo por execução, já que a origem só tem o estado atual).
- Criada a tabela `headcount_publico` no Neon e populada com o primeiro snapshot real (2026-09-10, 1.514 colaboradores ativos, 355 combinações de dimensões).
- **`app.py` agora lê do Neon** (`load_headcount_data()`), não mais do Databricks direto — removida toda a lógica de credenciais Databricks do app. Fallback pro mock local mantido.
- Filtros da barra lateral (Diretoria, Centro de Custo, Estado, Nível de Cargo) passaram a ser calculados a partir dos valores reais carregados, em vez de uma lista fixa — necessário porque a taxonomia real (14 diretorias reais) é bem diferente do placeholder mock.
- Adicionado servidor MCP da Neon (`https://mcp.neon.tech/mcp`) ao projeto para gerenciar o banco direto pelo Claude Code.
- Decisão de privacidade registrada: diretorias pequenas (ex. 2 pessoas) são publicadas com número exato por ora — ver CONTEXT.md.

## [0.4.0] - 2026-09-09

- Adicionados KPIs de crescimento em 3 janelas (1, 3 e 12 meses), calculados sobre o histórico completo, independente do filtro de data.
- Adicionada a seção "Quem mais mudou no último mês", com ranking de diretorias por variação de headcount (verde = cresceu, vermelho = caiu).
- Inspirado no dashboard interno de RH (`C:\Dev\Projetos\StreamlitRH\pages\1_Headcount.py`), adaptando só o que cabe no escopo público (sem admissões/desligamentos/saldo/dados demográficos).

## [0.3.0] - 2026-09-09

- Renomeado o agrupador "Unidade de negócio" para "Diretoria" (nome oficial confirmado).
- Adicionada a dimensão "Estado" como novo agrupador público (mock por enquanto).
- Removida a dimensão "Modelo de trabalho" (filtro, gráfico e coluna) por falta de utilidade.
- Confirmado escopo restrito a headcount, sem admissões/desligamentos/saldo.
- Atualizado o contrato Databricks para incluir `diretoria` e `state`, e remover `work_model`.

## [0.2.1] - 2026-09-09

- Reforçada a identidade visual com paleta completa (cores semânticas, gráficos e sidebar em azul petróleo escuro).
- Adicionados ícones Material às seções para facilitar a leitura rápida do painel.
- Adicionada barra de progresso nativa na leitura de participação por unidade.

## [0.2.0] - 2026-09-08

- Adicionados filtros e visualizações por centro de custo.
- Adicionados filtros e visualizações por nível de cargo.
- Atualizado o contrato de dados do Databricks com as novas dimensões agregadas.
- Atualizado o mock sintético para preservar os totais entre as dimensões.
- Consolidada a identidade visual com azul petróleo e verde da Pacaembu.

## [0.1.0] - 2026-09-08

- Criado mockup público de headcount em Streamlit.
- Adicionados filtros por data/período, unidade de negócio e modelo de trabalho.
- Adicionados KPIs de último snapshot, média, unidades e data de referência.
- Adicionados gráficos de evolução, alocação por unidade e modelo de trabalho.
- Adicionado download de resumo agregado em CSV.
- Criado loader com fallback para mock local e contrato preparado para Databricks.
- Registrada identidade visual inicial da Pacaembu em `.streamlit/config.toml`.
