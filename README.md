# Hermes Bot Inspector

Diagnósticos locais para o **Hermes Desktop**: escolha um quadro/cartão ou perfil/sessão, veja o histórico em timeline por tipo de evento, inspecione processos e capture pilhas Python com `py-spy`. Inclui recuperação **restrita a subprocessos Git pertencentes à execução escolhida**.

> Versão inicial **0.1.3 (pré-lançamento)**. Testes automatizados executados com bancos SQLite simulados; interface e leitura de timeline **testadas no Hermes Desktop 0.22.7 pelo usuário**; a correção de 404 e a recuperação ainda não foram validadas em Windows. Não é uma correção definitiva para o bug de `version_info.py`; apenas ajuda a identificá-lo e, em um caso estritamente verificável, pode liberar o processo Git.

## Compatibilidade e fontes

- Testado sintaticamente em Node 22 e Python 3.13; alvos: Windows e Hermes 0.22.7.
- UI usa **SDK oficial** `@hermes/plugin-sdk`: `ROUTES_AREA` + `SIDEBAR_NAV_AREA` (adiciona **Bot Inspector** ao menu lateral).
- Backend usa o `dashboard/manifest.json` documentado (FastAPI em `/api/plugins/hermes-kanban-inspector/`).
- Não exige API key, conta extra, LLM, serviço web ou configuração de diretórios no Hermes padrão `%LOCALAPPDATA%\hermes`.
- A captura de pilhas exige `py-spy` já disponível no PATH ou nas localizações inspecionadas; o restante funciona sem ele. O plugin **não baixa nem executa binários de terceiros automaticamente**.

## Instalação pelo GitHub

```powershell
hermes plugins install inayan60/hermes-kanban-inspector --enable
```

`--enable` habilita a metade **Agent**. O Desktop reconhece a metade `desktop/plugin.js`, mas o usuário pode precisar ativar o botão **Desktop** em **Capabilities → Plugins** (é uma decisão de confiança independente). Para carregar as rotas Python, reinicie o gateway uma vez após a instalação, pelo controle normal do Hermes. A entrada **Bot Inspector** aparecerá na barra lateral.

Também funciona via **Capabilities → Plugins → Install from Git** ou por um link `hermes://plugin/install?repo=inayan60/hermes-kanban-inspector&enable=1`, diretamente a partir deste repositório público. Não há como transformar um repositório inexistente em fonte de instalação.

### Para testar diretamente do ZIP antes de publicar

Extraia o pacote e execute a partir da pasta extraída:

```powershell
hermes plugins install "file:///C:/CAMINHO/hermes-kanban-inspector" --enable
```

Substitua `CAMINHO` pelo caminho real. Em **Capabilities → Plugins**, verifique as duas metades (Desktop / Agent). Reinicie o gateway após habilitar o backend. Não copie scripts para dentro da árvore `hermes-agent` nem edite `config.yaml` manualmente.

## Plugin Doctor e atualização da interface

O pacote inclui um `__init__.py` **sem tools ou hooks**, para passar pelo caminho de registro do `hermes plugins doctor`; a API continua declarada em `dashboard/manifest.json` e a interface em `desktop/plugin.js`. Não é necessário instalar tools nos perfis.

O Hermes Desktop usa uma **cópia** em `desktop-plugins/hermes-kanban-inspector/plugin.js`. O `hermes plugins update` pode atualizar o pacote Agent sem que essa cópia do Desktop seja recarregada imediatamente. Depois de atualizar, use **Capabilities → Plugins → Rescan** (ou reinicie o Hermes Desktop). Em seguida execute `hermes plugins doctor hermes-kanban-inspector` para verificar se o aviso `stale copy` desapareceu. Não copie manualmente arquivos nem edite configuração de perfis para contornar essa sincronização.

## 404 intermitente ao alternar perfis (Hermes Desktop)

O texto exato `404: {"detail":"Plugin not found"}` vem do gate de acesso a plugins do Hermes, não das consultas SQLite. O Hermes 0.22.7 pode encaminhar `ctx.rest` para o backend de um **perfil diferente**, que não tem a metade Agent autorizada. Este é um defeito conhecido do Hermes (veja [issue #134712](https://github.com/NousResearch/hermes-agent/issues/134712)).

Nesta versão, o plugin:

- Explica a causa provável e mostra como habilitar a metade Agent no backend/perfil adequado, preservando a timeline já carregada.
- Não apaga os resultados anteriores durante falhas de rede/404; **Atualizar** refaz a descoberta.
- Resolve corretamente a pasta compartilhada dos quadros quando `HERMES_HOME` aponta para `<instalacao>/profiles/<nome>`.

**Limite importante:** não é possível fazer o plugin ignorar os controles de autorização do próprio Hermes. Se a API retornar `Plugin not found`, habilite a metade Agent no perfil que está atendendo a requisição (em Capabilities → Plugins, seletor do perfil) ou na configuração do perfil que iniciou `hermes serve`. Reinicie o backend somente se a metade Agent tiver sido instalada após o servidor subir. Não habilite indiscriminadamente todos os perfis apenas para esconder o problema; o código do plugin pode ler sessões de vários perfis.

## Funções

| Função | Comportamento |
|---|---|
| Quadro → tarefa | Mostra status, owner, eventos e comentários; tenta localizar sessão via `owner_admitted`. |
| Bot → sessão | Lê apenas as mensagens persistidas naquele `state.db`. |
| Timeline | Filtros independentes para mensagens, terminal, read, write, outras ferramentas, resultados, eventos Kanban e comentários; busca e expansão dos detalhes. |
| Diagnóstico | PID do submitter (`worker_pid`), managed worker (`worker_bound`), gateway (`owner_admitted`), mensagens persistidas, tempo decorrido, heartbeat e processos descendentes. |
| `py-spy` | Snapshot sob demanda do PID Python selecionado; alerta para `_run_git` + `codex_cloudflare_headers`. |
| Patch Git | Apenas verifica a presença de `tempfile.TemporaryFile` e `capture_output=True` no `_run_git`; **não altera a instalação do Hermes**. |
| Destravar | Prévia de subprocesso Git **descendente comprovado** do submitter ou managed worker, comando `rev-parse`/`status`, identidade e data de criação; confirmação `ENCERRAR GIT`; rechecagem imediata antes de encerrar. Se não der para provar, a ação é recusada. |
| Exportação | Copia JSON sanitizado da timeline/diagnóstico visível para a área de transferência. |

## Como usar

1. Abra **Bot Inspector** no menu lateral.
2. Escolha **Quadro/tarefa** (ex.: `default` → `t_91dcef9e`) ou **Bot/sessão** (`lenora-rh` → sessão).
3. Selecione os filtros e carregue a **timeline**.
4. Para tarefa Kanban, clique em **Diagnosticar**. Zero mensagens não prova por si só travamento, mas ajuda a priorizar a inspeção.
5. Se houver suspeita, selecione o PID `agent.managed_worker` e clique **Capturar pilha (py-spy)**.
6. Somente para subprocesso Git comprovadamente vinculado ao worker, escolha **Prévia: destravar Git** e confirme digitando `ENCERRAR GIT`.

## Como a segurança funciona

- **Sem escrita nos bancos**: conexões SQLite `mode=ro`, `PRAGMA query_only` e consultas parametrizadas.
- **Sem executar `hermes` durante a coleta**: evita recursão, novos modelos e ruído de processo.
- **Sem coleta oculta**: UI só consulta quando a pessoa clica. Não há monitor automático em background.
- **Sem kill por similaridade**: `git.exe` com PPID desconectado do worker, como o PID `6188` do diagnóstico anterior, nunca é selecionado automaticamente.
- **Sem destravamento genérico**: o plugin não reinicia gateway, não dá `reclaim`, não mata Python e não altera patches do Hermes. O processo filho Git pode estar solto, reparented ou no Windows com dados incompletos; nesse caso a UI mostra que a recuperação automática não é elegível.
- **Recuperação pode ter impacto**: encerrar um Git pode fazer a run falhar. A prévia expira em 60 segundos e só permite a confirmação uma vez. Revalida task, run, PID, parentesco, criação e comando.
- **Redação de segredos é heurística**: revise o conteúdo antes de compartilhar ou copiar/exportar JSON. O plugin mostra trechos de mensagens e argumentos que podem conter dados internos.
- **Escopo de confiança**: desktop plugins não são sandboxed. Instale somente código revisado, de preferência fixando um commit auditado.
- **Backend do Hermes**: a metade Python só monta depois que a metade Agent é habilitada e o gateway carrega as rotas.

## Estrutura

```text
plugin.yaml
__init__.py
README.md
LICENSE
.github/workflows/test.yml
dashboard/manifest.json
dashboard/plugin_api.py
desktop/plugin.js
tests/test_plugin.py
```

## Executar testes

```bash
python -m pip install fastapi pydantic
python -m unittest discover -s tests -v
node tests/check_desktop.mjs
```

Os testes geram bancos SQLite descartáveis em `/tmp` e simulam processos. **Não executam nenhuma recuperação real**.

## Referências técnicas

- [Hermes Desktop Plugin SDK](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/developer-guide/desktop-plugin-sdk.md)
- [Hermes Plugins / instalação](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/user-guide/features/plugins.md)
- [Kanban / SQLite](https://github.com/NousResearch/hermes-agent/blob/v0.22.7/website/docs/user-guide/features/kanban.md)

## Próximos incrementos sugeridos

1. Compatibilidade comprovada em Windows 11/Hermes 0.22.7 com o SDK e os estados reais.
2. Modo `py-spy` por snapshots repetidos no mesmo PID e comparação de stacks.
3. Leitura opcional dos logs normalizados (não apenas mensagens persistidas) para enriquecer as tentativas que nem chegaram a entrar no `state.db`.
4. Diagnósticos de consumo por perfil (tokens, cache, modelos auxiliares) sem solicitar modelo para coletar métricas.
5. Exportação de pacote de suporte sanitizado com relatório Markdown e CSV de timeline.
6. Opção de patch Git com backup/rollback e compatibilidade por versão, **somente após validação contra o código instalado**.

Licença MIT. Use por sua conta e risco em ambiente de testes antes de operar em produção.