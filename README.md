# Hermes Bot Inspector

Plugin híbrido **Agent + Desktop** para auditar tarefas Kanban no Hermes 0.22.7 sem chamar modelos, sem executar novas tarefas e sem alterar os bancos do Hermes.

> **v0.2.0 — pré-lançamento.** Validações realizadas localmente em Python 3.13, Node 22 e SQLite de teste. A execução desta versão no Windows/Hermes Desktop 0.22.7 ainda precisa ser confirmada. A interface antiga funcionou parcialmente, mas esta versão é uma reformulação relevante.

## Instalar / atualizar

```powershell
hermes plugins install inayan60/hermes-kanban-inspector --enable
# OU, para quem já instalou:
hermes plugins update hermes-kanban-inspector
hermes plugins doctor hermes-kanban-inspector
```

No **Hermes Desktop → Capabilities → Plugins** habilite **Desktop** para `hermes-kanban-inspector` e o **Agent** no perfil de backend escolhido (normalmente `default`). Em seguida clique em **Rescan**. A metade Agent registra as rotas REST; a metade Desktop contribui com a entrada **Bot Inspector** na barra lateral. Não é necessário habilitar o Inspector em todos os bots para ler os bancos de sessão deles.

**Cópia antiga:** o Desktop carrega `%LOCALAPPDATA%\hermes\desktop-plugins\hermes-kanban-inspector\plugin.js`, que é uma cópia de `plugins\hermes-kanban-inspector\desktop\plugin.js`. O `Plugin Doctor` alerta se essa cópia estiver desatualizada. Execute **Capabilities → Plugins → Rescan** e reabra o Desktop; se o alerta persistir, verifique timestamp/marker em vez de editar o aplicativo manualmente.

**404 `Plugin not found`:** trata-se do controle de API de plugins do Hermes no backend de perfil que atendeu a requisição, não da inexistência de sessão. A UI apresenta diagnóstico, mas não contorna permissões do Hermes. O 404 pode reaparecer se o backend efetivo mudar de perfil; confira a habilitação Agent no backend conectado.

## Auditoria completa por cartão (nova)

Selecione **Quadro / cartão**, escolha `default` e o cartão, e clique **Auditar cartão**.

| Guia | Conteúdo |
|---|---|
| **Runs** | Todas as runs registradas, seus agentes, vínculo `owner_admitted`, modelos, sessões, quantidades de mensagens/tools, duração de cada run, estado histórico dos processos e erros. |
| **Tokens / modelos** | Uso por perfil, sessão, modelo, provider e task (`session_model_usage`), com entrada/saída/cache/leitura/escrita e número de chamadas. |
| **Timeline** | Eventos Kanban + comentários + mensagens + chamadas de ferramentas + resultados de **todas** as sessões vinculadas; filtros e busca. |
| **Artefatos** | Lista dos anexos; prévia sob demanda de `.md`, `.txt`, `.json`, `.csv`, `.log` com sanitização heurística e limite de 256 KiB. ZIP/PDF são apenas metadados. |
| **Erros e limites** | `worker_lost`, falhas de run, resultados com exit code, `worker_receipts`, trechos dos logs locais quando correlacionados, bloqueios, revisões e padrões de repetição. |
| **Processos / py-spy** | Diagnóstico e stack do **worker atual** apenas em cartão `running`; no caso de Git descendente comprovado, prévia e encerramento sob confirmação explícita. |

**Vínculo entre runs e sessões:** usa `task_events.owner_admitted` e `run_id`/`request_id`, com o perfil do registro de run ou do caminho `state.db` no evento. A sessão mais recente não substitui as demais. Filhos são incluídos somente quando há `sessions.parent_session_id` documentado; comentários e menções de terceiros não são transformados em sessões inventadas.

**Uso de tokens:** `session_model_usage` é preferido; na ausência da tabela ou linha usa-se `sessions` como fallback, **sem somar as duas fontes**. O total do cartão deduplica sessões. Cache, raciocínio e entrada/saída são métricas distintas; **não somar cache_read novamente ao input**. Atribuição a uma run é marcada como compartilhada se a mesma sessão estiver em várias runs. Nenhum valor de quota é atribuído exclusivamente ao cartão sem medições externas.

**Duração:** cada run usa `task_runs.ended_at - task_runs.started_at`; se faltar o término, usa explicitamente `worker_result.created_at` como fallback, sem confundir com a duração global `tasks.completed_at - tasks.created_at`. Em runs encerradas o PID é apenas histórico; `PID 0` nunca é considerado ativo. Um PID atual só é classificado como *ativo* quando sua fingerprint nativa do Hermes corresponde à gravada na run; caso contrário, `desconhecido` ou `encerrado/reutilizado`.

**Falhas:** `managed_worker_failed` + `exit_code=1` não revelam, por si sós, a stack técnica. O Inspector recupera erros registrados em `task_runs.error`, `worker_receipts` e um número limitado de linhas de logs locais contendo os identificadores do cartão/sessão. Se o erro não estiver persistido, comunica a limitação. Não afirma ter identificado uma causa que não está nas evidências.

## Segurança e limitações

- As consultas SQLite usam `mode=ro`, `query_only` e parâmetros, com limites de linhas por sessão/cartão; nenhuma escrita no Hermes, inferência, teste por modelo ou autoexecução.
- A maioria dos recursos só lê localmente. A opção **Encerrar subprocesso Git** é exceção explícita: usa prévia, token temporário, descendência do worker e confirmação `ENCERRAR GIT`; nunca encerra um PID sem vínculo conhecido. Essa operação pode causar falha na run. Não substitui correção definitiva do Git.
- `py-spy` é opcional e não é instalado automaticamente.
- Sanitização é **heurística**; conteúdo de ferramentas e artefatos pode conter informações internas. Revise antes de copiar/exportar JSON.
- `messages` limita a 4.500 entradas por sessão, `task_events` a 10.000 por cartão e anexos a 300, com avisos se a fonte exceder o limite. Apenas últimas linhas de arquivos de log predefinidos são pesquisadas, e a captura pode ser insuficiente.
- Não é possível reconstruir uso de modelos fora das sessões comprovadamente vinculadas nem dizer que a queda de quota de uma janela veio apenas deste cartão.
- O plugin não cria, fecha, reabre ou retoma Kanban; auditoria só observa registros existentes.

## Desenvolver / testar

```bash
python -m pip install fastapi pydantic
python -m unittest discover -s tests -v
node tests/check_desktop.mjs
```

`tests/check_desktop.mjs` valida parsing **ESM real** com `node --input-type=module --check` e simula o registro/sidebar e seis visões. Testes Python usam SQLite em diretório temporário, sem acesso ao Hermes da máquina de execução.

```
plugin.yaml
__init__.py
desktop/plugin.js
dashboard/manifest.json
dashboard/plugin_api.py
tests/test_plugin.py
tests/check_desktop.mjs
.github/workflows/test.yml
```

Documentação-base: [Hermes Desktop Plugin SDK](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/developer-guide/desktop-plugin-sdk.md) · [Kanban](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/user-guide/features/kanban.md) · [Sessões SQLite](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/developer-guide/session-storage.md).