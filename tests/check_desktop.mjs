// Runtime syntax smoke test for the native Hermes Desktop ESM plugin.
// Important: `node --check desktop/plugin.js` does NOT reliably check an ESM
// module in the current CI context. We must load the rewritten module.
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const root = new URL('../desktop/plugin.js', import.meta.url)
const source = readFileSync(root, 'utf8')
const dir = mkdtempSync(join(tmpdir(), 'hermes-inspector-esm-'))
try {
  const shims = {
    react: 'export default {createElement:(...args)=>({args})}; export const useEffect=()=>{}; export const useMemo=fn=>fn(); export const useState=x=>[typeof x===\'function\'?x():x,()=>{}];',
    'react/jsx-runtime': 'export const jsx=(...args)=>({args}); export const jsxs=jsx;',
    '@hermes/plugin-sdk': 'export const host={}; export const ROUTES_AREA="routes"; export const SIDEBAR_NAV_AREA="sidebar";'
  }
  const mapped = Object.fromEntries(Object.entries(shims).map(([specifier,code],index)=> {
    const path = join(dir, `shim${index}.mjs`)
    writeFileSync(path, code)
    return [specifier, pathToFileURL(path).href]
  }))
  const rewritten = source.replace(
    /(from\s*|import\s*\(\s*|import\s+)(['"])([^'"]+)\2/g,
    (whole,prefix,quote,specifier) => mapped[specifier]
      ? `${prefix}${quote}${mapped[specifier]}${quote}` : whole
  )
  const pluginPath = join(dir, 'plugin.mjs')
  writeFileSync(pluginPath, rewritten)
  const module = await import(pathToFileURL(pluginPath).href)
  const plugin = module.default
  const blocked = module.describeBackendError(new Error('404: {"detail":"Plugin not found"}'))
  if (!blocked.includes('Agent') || !blocked.includes('404 Plugin not found')) throw new Error('404 guidance missing')
  if (module.describeBackendError(new Error('timed out')) !== 'timed out') throw new Error('other errors must be preserved')
  if (plugin.id !== 'hermes-kanban-inspector') throw new Error('incorrect plugin ID')
  const contributions = []
  plugin.register({registerMany(items){contributions.push(...items)}})
  const route = contributions.find(x=>x.area==='routes' && x.data?.path==='/bot-inspector')
  const sidebar = contributions.find(x=>x.area==='sidebar' && x.data?.label==='Bot Inspector')
  if (!route || !sidebar) throw new Error('route or sidebar not registered')
  console.log('PASS: Desktop ESM import and navigation contributions')
} finally {
  rmSync(dir,{recursive:true,force:true})
}