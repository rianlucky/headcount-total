# Headcount público Pacaembu

Mockup de um guia público para consulta de headcount, alocação de mão de obra e evolução por data ou período. O painel não expõe gênero, salário, identificadores pessoais ou qualquer outra dimensão sensível.

## Rodar localmente

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

O app abre em `http://localhost:8501`.

## Contrato Neon

Sem credenciais configuradas, o painel usa um mock local determinístico. A fonte real é o **Neon** (Postgres), não o Databricks direto — o app nunca se conecta ao Databricks. Quem alimenta o Neon é o job batch semanal em `etl/` (ver seção abaixo e `CONTEXT.md` pra entender por quê).

### `.streamlit/secrets.toml`

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Depois edite `.streamlit/secrets.toml` com a connection string real:

```toml
[neon]
database_url = "postgresql://user:password@host/dbname?sslmode=require"
```

Esse arquivo já está no `.gitignore` — nunca vai parar no Git. Alternativa via variável de ambiente: `$env:NEON_DATABASE_URL = "<connection-string>"`.

**Como funciona:** ao iniciar, `load_headcount_data()` procura `st.secrets["neon"]["database_url"]` (ou a env var). Se a conexão falhar por qualquer motivo (credencial errada, rede, tabela ainda vazia), cai silenciosamente para o mock local — a página pública nunca quebra por causa da integração. O erro real aparece no terminal onde o `streamlit run` está rodando. O rodapé da barra lateral mostra "Fonte atual: Neon" ou "Mock local" conforme o que está em uso.

A tabela `headcount_publico` no Neon precisa entregar estas colunas agregadas (chave primária composta pelas 5 primeiras, pra permitir acumular snapshots semanais sem duplicar):

| Coluna | Tipo esperado | Descrição |
| --- | --- | --- |
| `snapshot_date` | date | Data de referência do snapshot |
| `diretoria` | text | Diretoria (via mapeamento manual de centro de custo, ver abaixo) |
| `area` | text | Área/departamento (via o mesmo mapeamento manual) |
| `job_level` | text | Nível de cargo agrupado (ver mapeamento abaixo) |
| `state` | text | Estado (nome por extenso) |
| `headcount` | integer | Quantidade agregada de colaboradores |

O loader usa `st.cache_data(ttl="15m")`. Filtros da barra lateral (Diretoria, Área, Estado, Nível de Cargo) são calculados a partir dos valores que realmente vieram na carga — não são mais uma lista fixa — então funcionam tanto com o mock quanto com dados reais.

## Job de extração (`etl/`)

`etl/extract_databricks_to_neon.py` roda semanalmente (Windows Task Scheduler, configurado por fora deste projeto) e faz:

1. **Lê** `rh.gold.fato_funcionario_ativo` no Databricks, já agregado por `GROUP BY` (nenhuma linha individual/PII sai do warehouse) — autenticando via OAuth user-to-machine (`auth_type="databricks-oauth"`, abre o navegador pra login, sem precisar de PAT nem Service Principal).
2. **Mapeia** `centro_de_custo` → diretoria/área pelo **mapeamento oficial da Central** (`core.mapeamento_diretoria` no Neon, planilha `_neon/mapeamento/Mapeamento Diretoria.xlsx` — o mesmo de todos os painéis; não o `nome_diretoria`/`nome_centro_custo` do gold — ver nota abaixo), `funcao_cargo` → nível de cargo público e `estado` (UF) → nome do estado por extenso.
3. **Grava** no Neon via upsert (`ON CONFLICT ... DO UPDATE`) — `fato_funcionario_ativo` só tem o estado atual (sem histórico), então cada execução soma um snapshot novo à tabela `headcount_publico`, que vai acumulando histórico semana a semana.

**`core.mapeamento_diretoria_especial`** corrige CCs "guarda-chuva" onde o mapeamento por CC sozinho não basta (ex.: CC da Presidência Executiva concentrando vários diretores de outras diretorias) — resolve por título de cargo (`by_position`) e, quando necessário, por `id_funcionario` (`by_person`, nunca por nome). Lógica de resolução compartilhada em `etl/mapping.py`.

**Histórico (`etl/backfill_from_neon_history.py`, rodado uma vez, reexecutável):** reconstrói set/2024–ago/2026 com quebra real por diretoria/área/estado/cargo, pessoa a pessoa, usando o mirror interno no Neon (`interno.fato_funcionario` + `interno.fato_funcionario_evol_cargos` — ver `etl/mirror_fatos_to_neon.py`, schema separado do público, com PII). Roda só contra o Neon, sem precisar do Databricks.

**Sobre o mapeamento:** o `nome_diretoria`/`nome_centro_custo` que vêm prontos no `rh.gold.fato_funcionario_ativo` ficam desatualizados depois de reorganizações. Desde 28/09/2026 o painel usa o mapeamento oficial da Central (antes tinha cópia própria em `etl/cc_mapping.json`/`special_mappings.json`, que ficou desatualizada). Centro de custo novo sem entrada cai em "Não informado": preencha a planilha oficial (o sync diário carrega).

Configuração em `etl/.env` (copiar de `etl/.env.example`, nunca commitar):

```dotenv
DATABRICKS_SERVER_HOSTNAME=...
DATABRICKS_HTTP_PATH=...
NEON_DATABASE_URL=...
```

Mapeamento de `funcao_cargo` pro nível de cargo público (confirmado com People Analytics em 2026-09-11 — ajustar em `etl/extract_databricks_to_neon.py`, constante `JOB_LEVEL_MAP`, se mudar):

| `funcao_cargo` (Databricks) | Nível público |
| --- | --- |
| Diretor, Presidente, Conselheiro | Direção |
| Gerente, Executivo | Gerência |
| Coordenador | Coordenação |
| Especialista, Engenheiro, Advogado | Especialista |
| Tudo o mais (Analista, Auxiliar, Supervisor, Técnico, Estagiário, ...) | Operacional |

**Decisão de privacidade (2026-09-11):** alguns agrupadores reais são pequenos (ex.: uma diretoria com 2 pessoas) — risco de reidentificação em teoria. Decisão do time: publicar os números exatos mesmo assim por enquanto (provisório). Revisar antes de qualquer divulgação mais ampla do painel.

## Taxonomia

Agrupadores públicos oficiais: **Diretoria**, **Área**, **Estado** e **Nível de Cargo**. Com dado real (Neon), os valores de Diretoria e Área são os nomes reais da empresa — não há mais uma lista fixa. `DIRETORIAS`/`AREAS_BY_DIRETORIA`/`STATES`/`JOB_LEVELS` em `app.py` seguem existindo só para gerar o mock local de demonstração.

## Referências usadas

- Padrões de cache, filtros e composição de dashboards da documentação local do Streamlit 1.61.
- Templates públicos de dashboard do repositório `streamlit/streamlit`.
- Dashboard de headcount existente em `C:\Dev\Projetos\StreamlitRH\pages` como referência de snapshots mensais e integração SQL.

## Polimento de 29/09/2026 (padrão da Central)

- **Fonte dos dados:** o app passou a ler `core.v_headcount_pessoas` (uma linha por atribuição,
  migração 018) em vez da tabela agregada `org.headcount_publico` (que continua sendo gravada).
  Mesma regra de data e mesmo mapeamento dos painéis Turnover, Dados Demográficos e Equidade: a
  evolução bate com a desses painéis. Nos meses passados, cada pessoa aparece com a diretoria/área/CC
  da atribuição.
- **Novidades:** filtro e gráficos por centro de custo; obras com os CCs 49 (SA) e 52 (LTDA) de
  mesmos 3 últimos dígitos somados; **curva de mobilização** de cada obra (SA e LTDA, mês a mês);
  **crescimento nos últimos 3 anos** (mesma data de cada ano, pessoas e %); quem mais
  cresceu/encolheu por área, diretoria ou CC; nível de cargo = nível de gerenciamento do cadastro
  (variações juntas), em ordem decrescente; sem desligamentos (o painel é mais aberto); mapa com bolhas semitransparentes; **efetivo** nominal em Excel (aba "Filtros" registra
  os filtros usados).
- **Visual:** login padrão, barra lateral padrão, cabeçalho com selos de atualização e filtros,
  cards com o recorte da bandeira, fonte Nunito.
- **Obras ativas:** agora conta obras (SA + LTDA juntas): 45 em 28/09/2026; antes contava só os CCs
  49 (42).
- **Presidência e Conselho:** aparecem separados, como nos demais painéis (antes eram somados).
- Conferência: `_neon/validacao/validar_paineis.py` (`painel_headcount`, mesmo `metricas.py`): total,
  diretoria, área, centro de custo, admissões e desligamentos ✅ em 29/09/2026.
- **Localização:** estado de quem já saiu vem da UF do local (o cadastro deixa vazio) e os locais
  antigos foram completados com `_neon/etl/completar_locais.py` (IBGE): sem estado em 09/2023 caiu
  de 455 para 55 pessoas.
