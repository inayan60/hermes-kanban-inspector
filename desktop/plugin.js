import React, { useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import { host, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

// ESM without a build step: only the three documented Hermes Desktop imports.
const H = React.createElement
const palette = {
  border: 'var(--ui-border, #353a42)',
  panel: 'var(--ui-bg-secondary, #1b2026)',
  text: 'var(--ui-text-primary, #ebedf0)',
  muted: 'var(--ui-text-secondary, #8e98a5)',
  accent: 'var(--ui-accent, #6d9feb)',
}
const styles = {
  page: { width: '100%', height: '100%', overflowY: 'auto', color: palette.text, padding: '22px', fontSize: 13 },
  header: { display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 },
  row: { display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' },
  panel: { background: palette.panel, border: `1px solid ${palette.border}`, borderRadius: 10, padding: 16, minWidth: 0 },
  label: { fontSize: 11, letterSpacing: '.055em', fontWeight: 700, color: palette.muted, marginBottom: 6 },
  control: { minWidth: 120, maxWidth: '100%', padding: '8px 10px', background: 'var(--ui-bg, #111418)', color: palette.text, border: `1px solid ${palette.border}`, borderRadius: 7, fontSize: 12 },
  btn: { padding: '8px 12px', color: palette.text, background: 'var(--ui-bg, #15191e)', border: `1px solid ${palette.border}`, borderRadius: 7, cursor: 'pointer', fontSize: 12 },
  sub: { color: palette.muted, fontSize: 12 },
  mono: { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', fontSize: 11 },
  code: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '8px 0 0', fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11, lineHeight: 1.5, color: palette.muted },
}
const FILTERS = [
  ['assistant', 'Mensagens'], ['user', 'Usuário'], ['terminal', 'Terminal'],
  ['read', 'Leituras'], ['write', 'Escritas'], ['tool', 'Outras tools'],
  ['result', 'Resultados'], ['board', 'Kanban'], ['comment', 'Comentários'],
]
const glyph = { assistant: '◈', user: '●', terminal: '⌘', read: '↗', write: '✎', tool: '⚙', result: '↳', board: '▦', comment: '▧', message: '◈' }
const stamp = (s) => s ? new Date(s).toLocaleString('pt-BR', {hour: '2-digit', minute: '2-digit', second: '2-digit', day: '2-digit', month: '2-digit'}) : '—'
const primaryButton = (children, onClick, disabled = false) => H('button', {type:'button', style: {...styles.btn, borderColor: palette.accent, opacity: disabled ? 0.5 : 1}, disabled, onClick}, children)
const secondaryButton = (children, onClick, disabled = false) => H('button', {type:'button', style: {...styles.btn, opacity: disabled ? 0.5 : 1}, disabled, onClick}, children)
const title = (s) => H('div', {style: {fontSize: 15, fontWeight: 700, marginBottom: 12}}, s)
const small = (s) => H('span', {style: styles.sub}, s)
const itemSelect = (label, value, onChange, entries) => H('label', {style: {display:'flex', flexDirection:'column', flex: '1 1 170px', minWidth: 150}},
  H('span', {style: styles.label}, label),
  H('select', {value, onChange: (e) => onChange(e.target.value), style: {...styles.control, width:'100%'}},
    H('option', {value: ''}, 'Selecione…'), ...entries.map(o=>H('option', {key: o.value, value: o.value}, o.label))))

function Inspector({ctx}) {
  const [inventory, setInventory] = useState({boards:[], profiles:[]})
  const [mode, setMode] = useState('board')
  const [board, setBoard] = useState('default')
  const [task, setTask] = useState('')
  const [profile, setProfile] = useState('')
  const [session, setSession] = useState('')
  const [targets, setTargets] = useState([])
  const [items, setItems] = useState([])
  const [report, setReport] = useState(null)
  const [diagnosis, setDiagnosis] = useState(null)
  const [stack, setStack] = useState(null)
  const [targetPid, setTargetPid] = useState('')
  const [recovery, setRecovery] = useState(null)
  const [recoveryText, setRecoveryText] = useState('')
  const [filter, setFilter] = useState(() => new Set(['assistant', 'user', 'terminal', 'read', 'write', 'tool', 'board', 'comment']))
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(new Set())

  async function request(path, opts) {
    try { setError(''); return await ctx.rest(path, opts) }
    catch(e) { setError(String(e?.message || e)); throw e }
  }
  useEffect(() => {
    let alive = true
    ctx.rest('/inventory').then(x => {if (!alive) return; setInventory(x); setBoard(x.boards.includes('default')?'default':x.boards[0]||''); setProfile(x.profiles.includes('default')?'default':x.profiles[0]||'')})
      .catch(e=>{ if(alive) setError(`Backend indisponível: ${String(e?.message || e)}. Ative a metade Agent do plugin e reinicie o gateway.`) })
    return ()=>{alive = false}
  }, [ctx])
  useEffect(()=>{
    let alive = true; setTargets([]); setTask(''); setSession(''); setItems([]); setReport(null); setDiagnosis(null); setRecovery(null); setStack(null)
    const path = mode === 'board' ? (board?`/tasks?board=${encodeURIComponent(board)}`:null) : (profile?`/sessions?profile=${encodeURIComponent(profile)}`:null)
    if(path) ctx.rest(path).then(x=>{if(alive)setTargets(x.items||[]) }).catch(e=>{if(alive)setError(String(e?.message||e))})
    return()=>{alive=false}
  },[ctx, mode, board, profile])
  async function loadTimeline() {
    const t = mode==='board' ? task : session
    if(!t)return
    setBusy('timeline'); setRecovery(null); setStack(null); setDiagnosis(null)
    try {
      const q = mode==='board' ? `mode=board&board=${encodeURIComponent(board)}&task_id=${encodeURIComponent(task)}` : `mode=profile&profile=${encodeURIComponent(profile)}&session_id=${encodeURIComponent(session)}`
      const r = await request(`/timeline?${q}`); setReport(r); setItems(r.timeline||[])
    } catch {} finally {setBusy('')}
  }
  async function inspect() {
    if(mode!=='board'||!task)return
    setBusy('diagnosis'); setStack(null);setRecovery(null)
    try{const r=await request(`/diagnose?board=${encodeURIComponent(board)}&task_id=${encodeURIComponent(task)}`);setDiagnosis(r); setTargetPid(String(r.managed_pid||r.worker_pid||''))}
    catch{}finally{setBusy('')}
  }
  async function trace(){if(!targetPid)return;setBusy('stack');try{setStack(await request(`/stack?pid=${encodeURIComponent(targetPid)}`))}catch{}finally{setBusy('')}}
  async function preview(){setBusy('preview');setRecovery(null);setRecoveryText('');try{setRecovery(await request('/recover/preview',{method:'POST',body:{board,task_id:task}}))}catch{}finally{setBusy('')}}
  async function apply(){if(recoveryText!=='ENCERRAR GIT'||!recovery?.token)return;setBusy('recover');try{const r=await request('/recover/apply',{method:'POST',body:{token:recovery.token,confirm:recoveryText}});setRecovery({available:false,reason:r.message});await inspect()}catch{}finally{setBusy('')}}
  async function copyReport(){const data=JSON.stringify({exported_at:new Date().toISOString(),mode,board,task,profile,session,summary:report,diagnosis,stack},null,2); const ok=await ctx.os.writeClipboard(data);if(!ok)setError('Não foi possível copiar para a área de transferência.')}
  const display = useMemo(()=>items.filter(x=>filter.has(x.kind)).filter(x=>!search||`${x.title} ${x.detail}`.toLowerCase().includes(search.toLowerCase())),[items,filter,search])
  const selectValue = mode==='board'?task:session
  const updateSelection=(x)=>{if(mode==='board')setTask(x);else setSession(x);setItems([]);setReport(null);setDiagnosis(null);setRecovery(null);setStack(null)}
  const toggleFilter=(kind)=>setFilter(old=>{const s=new Set(old);s.has(kind)?s.delete(kind):s.add(kind);return s})
  const toggleExpanded=(i)=>setExpanded(old=>{const s=new Set(old);s.has(i)?s.delete(i):s.add(i);return s})
  const options = targets.map(x=>({value:x.id,label:mode==='board'?`${x.id} · ${x.title} [${x.status}]`: `${x.title||x.id} ${x.source?`[${x.source}]`:''}`}))

  return H('main',{style:styles.page},
    H('div',{style:styles.header}, H('div',null,H('h1',{style:{fontSize:23,fontWeight:750,margin:'0 0 3px'}},'Bot Inspector'),small('Diagnóstico local • leitura de sessões e Kanban • sem chamadas ao modelo')),
      H('div',{style:styles.row}, small(`v${inventory.version||'0.1.1'}`),secondaryButton('Copiar JSON',copyReport,!report&&!diagnosis),secondaryButton('Atualizar',()=>{loadTimeline();if(mode==='board'&&diagnosis)inspect()},!selectValue))),
    error&&H('div',{role:'alert',style:{border:'1px solid #8d4c40',background:'#391f1b',color:'#ffd2cb',padding:12,borderRadius:8,marginBottom:12}},error),
    H('section',{style:{...styles.panel,marginBottom:12}},
      H('div',{style:{...styles.row,marginBottom:14}},
        primaryButton('Quadro / tarefa',()=>setMode('board'),false),
        secondaryButton('Bot / sessão',()=>setMode('profile'),false),
        small(`Modo atual: ${mode==='board'?'Kanban':'Bot'}`)),
      H('div',{style:styles.row},
        mode==='board' ? itemSelect('QUADRO',board,setBoard,inventory.boards.map(v=>({value:v,label:v}))) : itemSelect('BOT / PROFILE',profile,setProfile,inventory.profiles.map(v=>({value:v,label:v}))),
        itemSelect(mode==='board'?'TAREFA':'SESSÃO',selectValue,updateSelection,options),
        H('div',{style:{alignSelf:'end'}},primaryButton(busy==='timeline'?'Lendo…':'Carregar timeline',loadTimeline,!selectValue||!!busy)),
        mode==='board' && H('div',{style:{alignSelf:'end'}},secondaryButton('Diagnosticar',inspect,!task||!!busy)))),
    H('section',{style:{...styles.panel,marginBottom:12}},
      H('div',{style:styles.header},title(`Timeline ${report?`· ${report.total_messages||0} mensagens persistidas`:''}`),
        H('input',{value:search,onChange:e=>setSearch(e.target.value),placeholder:'Buscar nos eventos…',style:{...styles.control,flex:'1 1 220px'}})),
      H('div',{style:{...styles.row,gap:7,marginBottom:14}},...FILTERS.map(([key,label])=>H('label',{key,style:{...styles.btn,padding:'5px 9px',whiteSpace:'nowrap',borderColor:filter.has(key)?palette.accent:palette.border,cursor:'pointer'}},
        H('input',{type:'checkbox',checked:filter.has(key),onChange:()=>toggleFilter(key),style:{marginRight:5}}),label))),
      !report ? H('div',{style:styles.sub},'Selecione uma tarefa ou sessão e clique em Carregar timeline.'):
      H('div',null, H('div',{style:{...styles.sub,marginBottom:10}},`${display.length} eventos visíveis · sessão: ${report.session_id||'não vinculada'} · ${report.executions?.length?report.executions.map(x=>`${x.status} / seq ${x.last_sequence}`).join(', '):'sem execução registrada'}`),
        display.length===0?H('p',{style:styles.sub},'Nenhuma ocorrência com esses filtros (ou nenhum passo foi persistido).'):
        H('div',{style:{maxHeight:540,overflowY:'auto',paddingLeft:4}},...display.map((x,i)=>H('div',{key:`${i}-${x.title}`,style:{display:'grid',gridTemplateColumns:'12px 65px minmax(0, 1fr)',gap:11,borderLeft:`1px solid ${palette.border}`,padding:'10px 4px 10px 15px'}},
          H('span',{style:{color:palette.accent}},glyph[x.kind]||'·'),
          H('span',{style:{...styles.mono,color:palette.muted}},x.time?stamp(x.time).split(' ').pop():'—'),
          H('div',{style:{minWidth:0}},H('button',{type:'button',onClick:()=>toggleExpanded(i),style:{background:'transparent',border:0,padding:0,color:palette.text,fontWeight:650,cursor:'pointer',textAlign:'left',maxWidth:'100%',overflowWrap:'anywhere'}},`${x.kind.toUpperCase()} · ${x.title}`),
            H('p',{style:{...styles.code,display:expanded.has(i)?'block':'-webkit-box',WebkitLineClamp:expanded.has(i)?undefined:2,WebkitBoxOrient:'vertical',overflow:'hidden'}},x.detail||'—'))))))),
    mode==='board'&&H('section',{style:{...styles.panel,marginBottom:12}},
      title('Saúde do worker e recuperação'),
      !diagnosis?small('Clique em Diagnosticar para verificar processos, progresso, patch Git e heartbeat.'):
      H('div',null,
        H('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(170px,1fr))',gap:8,marginBottom:12}},
          ...[
            ['STATUS',diagnosis.status||'—'],['RUN',`#${diagnosis.run_id||'—'}`],['MENSAGENS',String(diagnosis.message_count??'?')],
            ['WORKER',`${diagnosis.worker_pid||'—'} / ${diagnosis.worker_alive===true?'ativo':diagnosis.worker_alive===false?'ausente':'indefinido'}`],
            ['MANAGED WORKER',String(diagnosis.managed_pid||'—')],['HEARTBEAT',diagnosis.last_heartbeat_at?stamp(diagnosis.last_heartbeat_at):'não registrado'],
            ['PATCH GIT',diagnosis.patch?.patched?'presente':diagnosis.patch?.file_found?'ausente':'não verificado'],
            ['PY-SPY',diagnosis.py_spy_available?'disponível':'não localizado']
          ].map(([k,v])=>H('div',{key:k,style:{border:`1px solid ${palette.border}`,padding:'10px',borderRadius:7}},H('div',{style:styles.label},k),H('div',{style:{fontWeight:600}},v)))),
        H('p',{style:{...styles.sub,color:diagnosis.suspected_stall?'#e6ab67':palette.muted}},diagnosis.assessment),
        H('p',{style:styles.sub},`${diagnosis.verified_git_descendants?.length||0} Git vinculados ao worker · ${diagnosis.unrelated_git_count||0} Git sem vínculo confirmado`),
        H('div',{style:{...styles.row,marginBottom:12}},
          itemSelect('PYTHON PID',targetPid,setTargetPid,(diagnosis.related_processes||[]).filter(x=>String(x.name).toLowerCase().startsWith('python')).map(x=>({value:String(x.pid),label:`${x.pid} · ${x.command?.includes('managed_worker')?'managed_worker':x.name}`}))),
          H('div',{style:{alignSelf:'end'}},primaryButton(busy==='stack'?'Analisando…':'Capturar pilha (py-spy)',trace,!targetPid||!!busy))),
        stack&&H('div',{style:{marginBottom:12}},H('div',{style:{color:stack.git_header_deadlock?'#e6ab67':palette.muted}},stack.git_header_deadlock?'Padrão Git/header Codex identificado':'Pilha capturada · sem correspondência conclusiva'),H('pre',{style:{...styles.code,maxHeight:230,overflow:'auto',border:`1px solid ${palette.border}`,padding:10,borderRadius:7}},stack.stack)),
        H('div',{style:{...styles.row,marginBottom:12}},secondaryButton(busy==='preview'?'Verificando…':'Prévia: destravar Git',preview,!!busy)),
        recovery&&H('div',{style:{border:`1px solid ${recovery.available?'#a4773d':palette.border}`,borderRadius:7,padding:12}},
          H('p',{style:styles.sub},recovery.available?`Alvo confirmado: PID ${recovery.target?.pid} · ${recovery.target?.command}`:recovery.reason),
          recovery.available&&H('div',{style:{...styles.row,marginTop:8}},
            H('input',{value:recoveryText,onChange:e=>setRecoveryText(e.target.value),placeholder:'Digite ENCERRAR GIT',style:styles.control}),
            primaryButton('Encerrar subprocesso Git',apply,recoveryText!=='ENCERRAR GIT'||!!busy)))))
  )
}

export default {
  id: 'hermes-kanban-inspector',
  name: 'Bot Inspector',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {id: 'page', area: ROUTES_AREA, data: {path: '/bot-inspector'}, render: () => jsx(Inspector, {ctx})},
      {id: 'sidebar', area: SIDEBAR_NAV_AREA, order: 185, data: {path: '/bot-inspector', label: 'Bot Inspector', codicon: 'pulse'}},
    ])
  },
}