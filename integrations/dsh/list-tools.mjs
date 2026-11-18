/** Inspect registered evokit MCP tools and skills without creating an agent or requesting a model. */
import { execFileSync } from 'node:child_process'
import { dirname } from 'node:path'
export const name = 'evokit-list-tools'
export const inject = ['tools']

const expectedSkills = ['evolve', 'meta', 'onboard']

export function apply(ctx) {
  const exit = ctx.get('appExit')
  if (!exit) throw new Error('DSH launcher exit service is required')
  const command = process.env.EVOKIT_MCP_COMMAND || 'evokit'
  const expected = JSON.parse(execFileSync(command,
    ['tools', '--profile', 'improver'], { encoding: 'utf8', timeout: 30000 }))
    .map(tool => `mcp__evokit__${tool.name}`).sort()
  const deadline = Date.now() + 30000
  let busy = false
  let done = false
  const finish = (code) => {
    done = true
    clearInterval(timer)
    exit(code)
  }
  const check = async () => {
    const names = ctx.tools.definitions().map(tool => tool.name)
      .filter(name => name.startsWith('mcp__evokit__')).sort()
    const registry = ctx.get('skills')
    const found = registry === undefined ? []
      : (await registry.list({ cwd: process.cwd() })).filter(skill => skill.provider === 'evokit')
    const skills = found.map(skill => skill.name).sort()
    const skillRoots = [...new Set(found.filter(skill => skill.path).map(skill => dirname(dirname(skill.path))))]
    if (done) return
    if (JSON.stringify(names) === JSON.stringify(expected)
      && JSON.stringify(skills) === JSON.stringify(expectedSkills)) {
      console.log(JSON.stringify({ tools: names, skills, skillRoots, modelRequests: 0 }))
      finish(0)
    } else if (Date.now() >= deadline) {
      console.error(`Expected tools ${JSON.stringify(expected)} and skills ${JSON.stringify(expectedSkills)}; `
        + `found ${JSON.stringify(names)} and ${JSON.stringify(skills)}`)
      finish(1)
    }
  }
  const timer = setInterval(() => {
    if (busy || done) return
    busy = true
    check().catch(error => {
      console.error(String(error))
      finish(1)
    }).finally(() => { busy = false })
  }, 100)
  ctx.effect(() => () => clearInterval(timer), 'evokit-list-tools.timer')
}
