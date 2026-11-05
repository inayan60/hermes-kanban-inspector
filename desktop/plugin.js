import React, { useEffect, useMemo, useState } from 'react'
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

// Hermes Desktop runtime ESM; no bundler, LLM calls or ambient filesystem access.
const H = React.createElement
const C = {
  bg: 'var(--ui-bg-secondary, #1b222d)',
  fg: 'var(--ui-text-primary, #f1f4fa)',
  muted: 'var(--ui-text-secondary, #a6b0c1)',
  border: 'var(--ui-border, #334052)',
  accent: 'var(--ui-accent, #5e95f1)',
  danger: '#e6a0a0',
}
const ST = {
  page: { width: '100%', height: '100%', overflowY: 'auto', padding: '20px', color: C.fg, fontSize: 13 },
  row: {display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 9},
  card: {background: C.bg, border: `1px solid ${C.border}`, borderRadius: 10, padding: 15, marginBottom: 12},
  caption: {color: C.muted, fontSize: 11},
  input: {background: 'var(--ui-bg, #111720)', border: `1px solid ${C.border}`, borderRadius: 6, color: C.fg, padding: '8px 10px', maxWidth: '100%', minWidth: 130, fontSize: 12},
  button: {background: 'var(--ui-bg, #111720)', border: `1px solid ${C.border}`, borderRadius: 6, color: C.fg, padding: '8px 12px', fontSize: 12, cursor: 'pointer'},
  mono: {fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'},
  grid: {display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(165px, 1fr))', gap: 8},
}
const KINDS = [
  ['board','Kanban'],['comment','Comentários'],['assistant','Mensagens'],['user','Usuário'],
  ['terminal','Terminal'],['read','Leitura'],['write','Escrita'],['tool','Outras tools'],
  ['result','Resultados'],['message','Sistema'],
]
const fmt = n => n == null ? '—' : Number(n).toLocaleString('pt-BR')
const dur = n => n == null ? 'não registrado' : `${Number(n).toLocaleString('pt-BR', {maximumFractionDigits:1})} s`
const localTime = raw => {
  if (!raw) return '—'
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('pt-BR', {hour12:false})
}
function Button({children, onClick, disabled, primary, danger}) {
  return H('button', {type:'button', onClick, disabled, style:{...ST.button, borderColor: primary ? C.accent : danger ? C.danger : C.border, opacity: disabled ? .5 : 1}}, children)
}
function Select({label, value, onChange, options, style}) {
  return H('label', {style:{display:'flex', flexDirection:'column', gap:6, flex:'1 1 175px', minWidth:140, ...style}},
    H('span', {style:ST.caption}, label),
    H('select', {value, onChange:e=>onChange(e.target.value), style:{...ST.input, width:'100%'}},
      H('option', {value:''}, 'Selecione…'), ...options.map(o=>H('option', {key:String(o.value), value:o.value}, o.label))))
}
function Tile({label, value, note}) {
  return H('div', {style:{padding:10, border:`1px solid ${C.border}`, borderRadius:7, minWidth:0}},
    H('div', {style:ST.caption}, label), H('div', {style:{fontSize:17, fontWeight:700, marginTop:5, overflowWrap:'anywhere'}}, String(value)),
    note && H('div',{style:{...ST.caption, marginTop:5}},note))
}
function Heading({children}) { return H('h2', {style:{fontSize:15, margin:'0 0 12px', fontWeight:700}}, children) }
function Tag({children, bad}) { return H('span',{style:{border:`1px solid ${bad?C.danger:C.border}`, borderRadius:5, padding:'3px 6px', fontSize:11, color:bad?C.danger:C.muted}},children) }
function Empty({children}) {return H('p',{style:ST.caption},children)}
function Details({title, children, open}) {
  return H('details',{open, style:{marginTop:9, borderTop:`1px solid ${C.border}`, paddingTop:8}},
    H('summary',{style:{cursor:'pointer', fontWeight:650}},title),H('div',{style:{marginTop:8}},children))
}
const summaryErr = e => {
  const msg=String(e?.message || e || 'Erro desconhecido')
  return /404[\s\S]*plugin not found/i.test(msg)
    ? 'O backend que recebeu esta requisição não habilitou a API do Bot Inspector (404). Confira Capabilities → Plugins → perfil de backend → Agent. Alternar Bot/sessão não deveria exigir instalar o Inspector no bot consultado. Confira também a sincronização Desktop/Agent.'
    : msg
}
function Inspector({ctx}) {
  const [mode,setMode]=useState('board')
  const [inventory,setInventory]=useState({boards:[],profiles:[]})
  const [board,setBoard]=useState('default')
  const [task,setTask]=useState('')
  const [profile,setProfile]=useState('default')
  const [session,setSession]=useState('')
  const [targets,setTargets]=useState([])
  const [audit,setAudit]=useState(null)
  const [individual,setIndividual]=useState(null)
  const [view,setView]=useState('runs')
  const [selectedRun,setSelectedRun]=useState('all')
  const [search,setSearch]=useState('')
  const [filters,setFilters]=useState(()=>new Set(KINDS.map(x=>x[0])))
  const [error,setError]=useState('')
  const [busy,setBusy]=useState('')
  const [diagnosis,setDiagnosis]=useState(null)
  const [stack,setStack]=useState(null)
  const [pid,setPid]=useState('')
  const [recovery,setRecovery]=useState(null)
  const [confirm,setConfirm]=useState('')
  const [artifact,setArtifact]=useState(null)
  const [rev,setRev]=useState(0)
  async function api(path, opts) {
    try {const result=await ctx.rest(path,opts);setError('');return result}
    catch(e) {setError(summaryErr(e));throw e}
  }
  useEffect(()=>{
    let active=true
    ctx.rest('/inventory').then(x=>{if(active){setInventory(x);setBoard(b=>x.boards.includes(b)?b:x.boards[0]||'');setProfile(p=>x.profiles.includes(p)?p:x.profiles[0]||'');setError('')}})
      .catch(e=>{if(active)setError(summaryErr(e))})
    return()=>{active=false}
  },[ctx,rev])
  useEffect(()=>{
    let active=true
    setTargets([])
    const path = mode==='board' ? (board?`/tasks?board=${encodeURIComponent(board)}`:null)
                                : (profile?`/sessions?profile=${encodeURIComponent(profile)}`:null)
    if(path)ctx.rest(path).then(x=>{if(active){setTargets(x.items||[]);setError('')}})
      .catch(e=>{if(active)setError(summaryErr(e))})
    return()=>{active=false}
  },[ctx,mode,board,profile,rev])
  function clear(){setAudit(null);setIndividual(null);setDiagnosis(null);setStack(null);setRecovery(null);setArtifact(null);setSelectedRun('all')}
  async function load() {
    const id=mode==='board'?task:session
    if(!id)return
    setBusy('load');clear()
    try {
      if(mode==='board'){
        const x=await api(`/audit?board=${encodeURIComponent(board)}&task_id=${encodeURIComponent(task)}`)
        setAudit(x);setView('runs')
      }else{
        const x=await api(`/timeline?mode=profile&profile=${encodeURIComponent(profile)}&session_id=${encodeURIComponent(session)}`)
        setIndividual(x);setView('timeline')
      }
    }catch{} finally{setBusy('')}
  }
  async function inspect() {
    if(!task || !audit)return
    setBusy('diagnose')
    try {const r=await api(`/diagnose?board=${encodeURIComponent(board)}&task_id=${encodeURIComponent(task)}`);setDiagnosis(r);setPid(String(r.managed_pid>0?r.managed_pid:r.worker_pid>0?r.worker_pid:''))}
    catch{}finally{setBusy('')}
  }
  async function spy(){if(!pid)return;setBusy('spy');try{setStack(await api(`/stack?pid=${encodeURIComponent(pid)}`))}catch{}finally{setBusy('')}}
  async function preview(){setBusy('preview');setRecovery(null);setConfirm('');try{setRecovery(await api('/recover/preview',{method:'POST',body:{board,task_id:task}}))}catch{}finally{setBusy('')}}
  async function recover(){if(confirm!=='ENCERRAR GIT'||!recovery?.token)return;setBusy('recover');try{const r=await api('/recover/apply',{method:'POST',body:{token:recovery.token,confirm}});setRecovery({available:false,reason:r.message});setConfirm('')}catch{}finally{setBusy('')}}
  async function previewArtifact(id){setBusy('artifact');setArtifact(null);try{setArtifact(await api(`/artifact?board=${encodeURIComponent(board)}&task_id=${encodeURIComponent(task)}&attachment_id=${id}`))}catch{}finally{setBusy('')}}
  async function copy(){try{const ok=await ctx.os.writeClipboard(JSON.stringify({exported_at:new Date().toISOString(),board,task,audit,individual,diagnosis,stack,artifact},null,2));if(!ok)setError('Falha ao copiar')}catch(e){setError(summaryErr(e))}}
  const chosenRuns=useMemo(()=>audit?.runs?.filter(r=>selectedRun==='all'||String(r.id)===selectedRun)||[],[audit,selectedRun])
  const visible=useMemo(()=>{
    const events=audit?.timeline||individual?.timeline||[]
    const q=search.trim().toLowerCase()
    return events.filter(e=>(selectedRun==='all'||!audit||String(e.run_id)===selectedRun) && filters.has(e.kind))
      .filter(e=>!q||`${e.title} ${e.detail} ${e.profile||''} ${e.run_id||''}`.toLowerCase().includes(q))
  },[audit,individual,search,selectedRun,filters])
  const usage=chosenRuns.flatMap(r=>r.usage||[])
  const sums={input:0,output:0,cache:0,write:0,calls:0}
  // Display global authoritative deduplicated totals only with every run selected.
  const globalUsage=audit?.summary?.usage
  if(globalUsage && selectedRun==='all'){
    sums.input=globalUsage.input_tokens;sums.output=globalUsage.output_tokens
    sums.cache=globalUsage.cache_read_tokens;sums.write=globalUsage.cache_write_tokens
    sums.calls=globalUsage.api_call_count
  }else{
    const seen=new Set()
    for(const u of usage){const key=`${u.profile}:${u.session_id}:${u.model}:${u.task}:${u.provider}`
      if(seen.has(key))continue;seen.add(key)
      sums.input+=Number(u.input_tokens||0);sums.output+=Number(u.output_tokens||0)
      sums.cache+=Number(u.cache_read_tokens||0);sums.write+=Number(u.cache_write_tokens||0)
      sums.calls+=Number(u.api_call_count||0)
    }
  }
  const tokenBox=()=>H('section',{style:ST.card},H(Heading,null,'Consumo registrado por sessão / modelo'),
    H('div',{style:ST.grid},H(Tile,{label:'Tokens entrada',value:fmt(sums.input)}),H(Tile,{label:'Tokens saída',value:fmt(sums.output)}),
      H(Tile,{label:'Cache leitura',value:fmt(sums.cache),note:'Exibido separadamente; não somar novamente à entrada'}),
      H(Tile,{label:'Cache escrita',value:fmt(sums.write)}),H(Tile,{label:'Chamadas API',value:fmt(sums.calls)})),
    H('div',{style:{marginTop:10}},
      usage.length===0?H(Empty,null,'Nenhum registro de uso atribuível encontrado.'):
      usage.map((u,i)=>H('div',{key:i,style:{borderTop:`1px solid ${C.border}`,padding:'7px 0'}},
        H('strong',null,`${u.profile} · ${u.model||'modelo não informado'}`),
        H('div',{style:ST.caption},`run #${selectedRun==='all'?audit.runs.find(r=>r.usage?.includes(u))?.id||'?':selectedRun} · sessão ${u.session_id} · ${u.provider||'provider não registrado'} · tarefa ${u.task||'—'} · fonte ${u.source}${u.shared_between_runs?' · sessão compartilhada':''}`),
        H('div',{style:ST.caption},`entrada ${fmt(u.input_tokens)} · saída ${fmt(u.output_tokens)} · cache lido ${fmt(u.cache_read_tokens)} · cache escrito ${fmt(u.cache_write_tokens)} · chamadas ${fmt(u.api_call_count)}`)))))
  const runView=()=>H('div',null,
    chosenRuns.length===0?H(Empty,null,'Nenhuma execução persistida.'):
    chosenRuns.map(r=>H('section',{key:r.id,style:ST.card},
      H('div',{style:{...ST.row,justifyContent:'space-between',marginBottom:10}},
        H('div',null,H('strong',{style:{fontSize:16}},`Run #${r.id} · ${r.profile||'agente não informado'}`),
          H('div',{style:ST.caption},`${localTime(r.start)} → ${localTime(r.end)}`)),
        H(Tag,{bad:r.errors?.some(x=>x.kind==='worker_lost')},`${r.status} · ${r.outcome||'sem outcome'}`)),
      H('div',{style:ST.grid},H(Tile,{label:'Duração da run',value:dur(r.duration_seconds),note:r.duration_source}),
        H(Tile,{label:'Mensagens',value:fmt(r.messages)}),H(Tile,{label:'Tools',value:fmt(r.tool_calls)}),
        H(Tile,{label:'Resultados',value:fmt(r.tool_results)}),H(Tile,{label:'Estado do processo',value:r.process_state}),
        H(Tile,{label:'Sessões ligadas',value:fmt(r.sessions?.length||0)})),
      H(Details,{title:`Sessões e modelos (${r.sessions?.length||0})`,open:true},
        ...(r.sessions||[]).map((s,i)=>H('div',{key:i,style:{marginBottom:10,padding:'9px',border:`1px solid ${C.border}`,borderRadius:6}},
          H('strong',null,`${s.profile} · ${s.id}`),H('div',{style:ST.caption},`${s.role} · ${s.model||'?'} · ${fmt(s.message_count)} mensagens · ${fmt(s.tool_calls)} chamadas`),
          s.message_truncated&&H(Tag,{bad:true},'Mensagens truncadas')))),
      (r.errors?.length||0)>0&&H(Details,{title:`Falhas e eventos do worker (${r.errors.length})`},
        ...(r.errors||[]).map((e,i)=>H('pre',{key:i,style:{...ST.mono,color:C.danger}},`${e.kind} · ${e.source}\n${e.payload}`))),
      (r.sessions||[]).some(s=>s.repeated_tool_signals?.length)&&H(Details,{title:'Padrões de chamadas repetidas'},
        ...(r.sessions||[]).flatMap(s=>(s.repeated_tool_signals||[]).map((e,i)=>H('p',{key:s.id+i,style:ST.caption},`${s.profile}: ${e.tool} ×${e.count} — ${e.argument_preview}`)))))))
  const timelineView=()=>H('section',{style:ST.card},H(Heading,null,`Timeline · ${fmt(visible.length)} eventos filtrados`),
    H('div',{style:{...ST.row,marginBottom:10}},H('input',{value:search,onChange:e=>setSearch(e.target.value),placeholder:'Buscar ferramenta, agente, erro, texto…',style:{...ST.input,flex:'1 1 220px'}})),
    H('div',{style:{...ST.row,marginBottom:12}},...KINDS.map(([k,label])=>H('label',{key:k,style:{...ST.caption,cursor:'pointer',padding:'4px',border:`1px solid ${C.border}`,borderRadius:4}},
      H('input',{type:'checkbox',checked:filters.has(k),onChange:()=>setFilters(prev=>{const s=new Set(prev);s.has(k)?s.delete(k):s.add(k);return s})}),` ${label}`))),
    H('div',{style:{maxHeight:760,overflowY:'auto'}},...visible.slice(0,1300).map((e,i)=>H('details',{key:i,style:{padding:'8px',borderBottom:`1px solid ${C.border}`}},
      H('summary',{style:{cursor:'pointer',display:'flex',flexWrap:'wrap',gap:10}},
        H('span',{style:{...ST.caption,minWidth:75}},localTime(e.time).split(' ').at(-1)),
        H('strong',null,e.kind.toUpperCase()),H('span',null,e.title),
        H('span',{style:ST.caption},e.run_id?`#${e.run_id}`:'cartão'),
        e.profile&&H('span',{style:ST.caption},e.profile)),
      H('pre',{style:{...ST.mono,color:C.muted,margin:'9px 0 0'}},e.detail||'—'))),
      visible.length>1300&&H(Empty,null,'Exibindo os primeiros 1.300 eventos filtrados. Refine a busca.')))
  const artifactView=()=>H('section',{style:ST.card},H(Heading,null,'Artefatos do cartão'),
    (audit?.artifacts||[]).length===0?H(Empty,null,'Sem anexos no registro.'): audit.artifacts.map(a=>H('div',{key:a.id,style:{...ST.row,justifyContent:'space-between',borderBottom:`1px solid ${C.border}`,padding:'8px 0'}},
      H('div',null,H('strong',null,a.filename),H('div',{style:ST.caption},`${fmt(a.bytes)} bytes · ${a.uploaded_by||'autor não informado'} · ${localTime(a.time)}`)),
      H(Button,{disabled:!a.previewable||!!busy,onClick:()=>previewArtifact(a.id)},a.previewable?'Ler conteúdo':'Binário: metadados'))),
    artifact&&H('div',{style:{marginTop:16}},H(Heading,null,artifact.filename),
      H('div',{style:ST.caption},artifact.notice),artifact.previewable&&H('pre',{style:{...ST.mono,maxHeight:600,overflow:'auto',padding:12,border:`1px solid ${C.border}`,borderRadius:6}},artifact.content)))
  const errorView=()=>H('section',{style:ST.card},H(Heading,null,'Falhas, diagnósticos e limites de evidência'),
    ...((audit?.findings||[]).map((e,i)=>H('p',{key:'f'+i},H(Tag,{bad:e.severity==='warning'},e.type),' ',e.text))),
    ...((audit?.runs||[]).filter(r=>(r.errors||[]).length).flatMap(r=>r.errors.map((e,i)=>H('details',{key:r.id+'-'+i,style:{padding:'8px',borderBottom:`1px solid ${C.border}`}},
      H('summary',{style:{cursor:'pointer'}},`#${r.id} · ${e.kind} · ${e.source}`),H('pre',{style:ST.mono},e.payload))))),
    (audit?.error_log_evidence||[]).length>0&&H(Details,{title:`Registros de log correlacionados (${audit.error_log_evidence.length})`},
      ...audit.error_log_evidence.map((e,i)=>H('pre',{key:i,style:ST.mono},`${e.source}\n${e.detail}`))),
    (audit?.limitations||[]).length>0&&H(Details,{title:`Limitações (${audit.limitations.length})`,open:true},
      ...audit.limitations.map((e,i)=>H('p',{key:i,style:ST.caption},e))),
    H('p',{style:ST.caption},'exit_code=1 e managed_worker_failed não identificam sozinhos a causa técnica; uma stack ou log vinculado é necessário.'))
  return H('main',{style:ST.page},
    H('header',{style:{...ST.row,justifyContent:'space-between',marginBottom:15}},
      H('div',null,H('h1',{style:{fontSize:23,margin:'0 0 4px'}},'Bot Inspector'),
        H('div',{style:ST.caption},'Auditoria local · leitura de runs, sessões, tools, tokens e artefatos · sem modelo')),
      H('div',{style:ST.row},H('span',{style:ST.caption},'v0.2.0'),H(Button,{disabled:!audit&&!individual,onClick:copy},'Copiar JSON'),
        H(Button,{onClick:()=>setRev(r=>r+1),disabled:!!busy},'Atualizar listas'))),
    error&&H('div',{role:'alert',style:{...ST.card,background:'#392321',color:'#ffd4ce',borderColor:'#994f46'}},error),
    H('section',{style:ST.card},
      H('div',{style:{...ST.row,marginBottom:12}},
        H(Button,{onClick:()=>{setMode('board');setSession('');clear()},primary:mode==='board'},'Quadro / cartão'),
        H(Button,{onClick:()=>{setMode('profile');setTask('');clear()},primary:mode==='profile'},'Bot / sessão'),
        H('span',{style:ST.caption},mode==='board'?'Auditoria de todas as runs do cartão':'Sessão individual (sem reconstrução por cartão)')),
      H('div',{style:ST.row},
        mode==='board'?H(Select,{label:'QUADRO',value:board,onChange:b=>{setBoard(b);setTask('');clear()},options:inventory.boards.map(x=>({value:x,label:x}))})
                       :H(Select,{label:'BOT / PERFIL',value:profile,onChange:p=>{setProfile(p);setSession('');clear()},options:inventory.profiles.map(x=>({value:x,label:x}))}),
        H(Select,{label:mode==='board'?'CARTÃO':'SESSÃO',value:mode==='board'?task:session,onChange:x=>{mode==='board'?setTask(x):setSession(x);clear()},
          options:targets.map(x=>({value:x.id,label:mode==='board'?`${x.id} · ${x.title} [${x.status}]`:`${x.title||x.id} · ${x.id}`}))}),
        H('div',{style:{alignSelf:'end'}},H(Button,{primary:true,disabled:!!busy||!(mode==='board'?task:session),onClick:load},
          busy==='load'?'Coletando…':mode==='board'?'Auditar cartão':'Carregar sessão')))),
    (audit||individual)&&H('div',null,
      audit&&H('section',{style:ST.card},
        H(Heading,null,`${audit.task_id} · ${audit.task.title}`),
        H('div',{style:ST.grid},H(Tile,{label:'Estado do cartão',value:audit.task.status}),
          H(Tile,{label:'Runs',value:fmt(audit.summary.run_count)}),
          H(Tile,{label:'Sessões vinculadas',value:fmt(audit.summary.sessions)}),
          H(Tile,{label:'Duração do cartão',value:dur(audit.summary.card_duration_seconds),note:audit.summary.card_duration_source}),
          H(Tile,{label:'Artefatos',value:fmt(audit.summary.artifacts)})),
        H('p',{style:{...ST.caption,marginTop:10}},audit.attribution_note),
        H(Details,{title:`Participantes (${audit.participants.length})`},
          ...audit.participants.map((p,i)=>H('p',{key:i,style:ST.caption},`${p.name} — ${p.roles.join(', ')} (${p.sources.join('; ')})`)))),
      audit&&H('section',{style:{...ST.card,display:'flex',flexWrap:'wrap',gap:8}},
        H(Select,{label:'FILTRAR POR RUN',value:selectedRun,onChange:setSelectedRun,style:{flex:'0 1 280px'},
          options:[{value:'all',label:'Todas as runs'},...audit.runs.map(r=>({value:String(r.id),label:`#${r.id} · ${r.profile} · ${r.status}`}))]}),
        ...[['runs','Runs'],['tokens','Tokens / modelos'],['timeline','Timeline'],['artifacts','Artefatos'],['errors','Erros e limites'],['health','Processos / py-spy']].map(([v,l])=>
          H('div',{key:v,style:{alignSelf:'end'}},H(Button,{primary:view===v,onClick:()=>setView(v)},l)))),
      (view==='runs'&&audit)&&runView(),
      (view==='tokens'&&audit)&&tokenBox(),
      (view==='timeline'||individual)&&timelineView(),
      (view==='artifacts'&&audit)&&artifactView(),
      (view==='errors'&&audit)&&errorView(),
      (view==='health'&&audit)&&H('section',{style:ST.card},
        H(Heading,null,'Processos e recuperação controlada'),
        H('p',{style:ST.caption},'Para runs terminadas, PIDs históricos não comprovam processo atual. Recuperação não é oferecida sem uma execução running.'),
        H(Button,{onClick:inspect,disabled:!!busy||audit.task.status!=='running'},'Diagnosticar execução ativa'),
        diagnosis&&H('div',{style:{marginTop:12}},
          H('div',{style:ST.grid},H(Tile,{label:'Run atual',value:'#'+(diagnosis.run_id||'?')}),
            H(Tile,{label:'PID worker',value:diagnosis.worker_pid||'ausente'}),
            H(Tile,{label:'Processo',value:diagnosis.worker_alive===true?'ativo':diagnosis.worker_alive===false?'encerrado':'desconhecido'}),
            H(Tile,{label:'Patch Git',value:diagnosis.patch?.patched?'presente':'não comprovado'})),
          H('p',{style:ST.caption},diagnosis.assessment),
          H('div',{style:ST.row},H(Select,{label:'PID PYTHON',value:pid,onChange:setPid,options:(diagnosis.related_processes||[])
            .filter(p=>p.pid>0&&String(p.name).toLowerCase().startsWith('python')).map(p=>({value:String(p.pid),label:`${p.pid} · ${p.name}`}))}),
            H(Button,{onClick:spy,disabled:!!busy||!pid},'Capturar pilha py-spy')),
          stack&&H('pre',{style:{...ST.mono,maxHeight:280,overflow:'auto'}},stack.stack),
          H('div',{style:{...ST.row,marginTop:12}},H(Button,{onClick:preview,disabled:!!busy},'Prévia: destravar Git')),
          recovery&&H('div',{style:{marginTop:10}},
            H('p',{style:ST.caption},recovery.available?`PID confirmado ${recovery.target.pid} · ${recovery.target.command}`:recovery.reason),
            recovery.available&&H('div',{style:ST.row},H('input',{value:confirm,onChange:e=>setConfirm(e.target.value),placeholder:'Digite ENCERRAR GIT',style:ST.input}),
              H(Button,{danger:true,onClick:recover,disabled:!!busy||confirm!=='ENCERRAR GIT'},'Encerrar Git confirmado')))))),
    !audit&&!individual&&H('section',{style:ST.card},H(Empty,null,'Selecione um cartão e clique em Auditar cartão. A auditoria reúne todas as runs e sessões comprovadamente vinculadas, sem alterar os perfis.')))
}
export default {
  id:'hermes-kanban-inspector', name:'Bot Inspector', defaultEnabled:true,
  register(ctx){ctx.registerMany([
    {id:'page',area:ROUTES_AREA,data:{path:'/bot-inspector'},render:()=>H(Inspector,{ctx})},
    {id:'sidebar',area:SIDEBAR_NAV_AREA,order:185,data:{path:'/bot-inspector',label:'Bot Inspector',codicon:'pulse'}},
  ])}
}