import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import vm from 'node:vm'

const file = path.join(import.meta.dirname, '..', 'desktop', 'plugin.js')
const raw = fs.readFileSync(file, 'utf8')
// Critical: Hermes imports this as a native ESM blob. CJS node --check can miss ESM errors.
const checked = spawnSync(process.execPath, ['--input-type=module', '--check'], {input: raw, encoding: 'utf8'})
assert.equal(checked.status, 0, checked.stderr)
const source = raw.replace(/^import .* from '(react|@hermes\/plugin-sdk)'\s*$/gm, '')
  .replace('export default {', 'globalThis.__plugin = {')
const fakeElements = []
const states = []
let cursor = 0
const mockReact = {
  createElement(type, props, ...children) {
    const node = {type, props: props||{}, children}
    fakeElements.push(node)
    return node
  },
  useState(initial) {
    const i = cursor++
    return [Object.prototype.hasOwnProperty.call(states,i) ? states[i] : typeof initial==='function'? initial():initial,
      ()=>{}]
  },
  useMemo(fn){ return fn() },
  useEffect(){},
}
const context = {React:mockReact,useEffect:mockReact.useEffect,useMemo:mockReact.useMemo,useState:mockReact.useState,
  ROUTES_AREA:'routes',SIDEBAR_NAV_AREA:'nav', Date, Set, String, Number, Math, console}
context.globalThis=context
vm.runInNewContext(source,context,{filename:'desktop/plugin.js'})
assert.equal(context.__plugin.id,'hermes-kanban-inspector')
const registrations = []
const ctx = {registerMany(items){registrations.push(...items)},rest(){return Promise.resolve({})},
  os:{writeClipboard:async()=>true}}
context.__plugin.register(ctx)
assert.equal(registrations.length,2)
const page = registrations.find(x=>x.area==='routes')
assert.ok(page && page.render)
assert.ok(registrations.find(x=>x.area==='nav'))
const element=page.render()
assert.equal(typeof element.type,'function')
function renderWith(stateMap){
  cursor=0
  for(const k of Object.keys(states))delete states[k]
  Object.assign(states,stateMap)
  const result=element.type(element.props)
  assert.ok(result && result.type==='main')
}
renderWith({})
// 6=profile, 7=session, 8=targets, 9=audit, 10=individual, 11=view, 12=selectedRun.
const audit={board:'default',task_id:'t_189a65a1',task:{title:'Inventário',status:'done'},
  summary:{run_count:3,sessions:3,artifacts:2,card_duration_seconds:1090,card_duration_source:'timestamps',
    usage:{input_tokens:200,output_tokens:40,cache_read_tokens:60,cache_write_tokens:0,api_call_count:6}},
  runs:[{id:165,profile:'laney-infra',status:'blocked',outcome:'blocked',start:'2026-11-05T19:16:00Z',
    end:'2026-11-05T19:22:00Z',duration_seconds:360,duration_source:'task_runs',process_state:'historical',
    messages:2,tool_calls:1,tool_results:1,errors:[],sessions:[{id:'local-165',profile:'laney-infra',
      role:'primary',model:'gpt-6.1-sol',message_count:2,tool_calls:1}],usage:[{session_id:'local-165',profile:'laney-infra',
      model:'gpt-6.1-sol',provider:'openai-codex',source:'session_model_usage',task:'agent',input_tokens:200,
      output_tokens:40,cache_read_tokens:60,cache_write_tokens:0,api_call_count:6}]}],
  participants:[{name:'laney-infra',roles:['run executor'],sources:['task_runs']}],
  artifacts:[{id:2,filename:'inventario.md',previewable:true,bytes:100,time:'2026-11-05T19:20:00Z'}],
  timeline:[{kind:'read',title:'file_read',detail:'doc.md',run_id:165,profile:'laney-infra',
    time:'2026-11-05T19:18:00Z'}],findings:[],limitations:[],error_log_evidence:[],attribution_note:'test'}
for(const view of ['runs','tokens','timeline','artifacts','errors','health']){
  renderWith({9:audit,11:view})
}
console.log('PASS: Desktop ESM parser, registry and six audit views')