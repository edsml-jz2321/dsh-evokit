/** Inspect registered MCP tools without creating an agent or requesting a model. */
import { execFileSync } from 'node:child_process'
export const name = 'evokit-list-tools'
export const inject = ['tools']

export function apply(ctx) {
  const exit = ctx.get('appExit')
  if (!exit) throw new Error('DSH launcher exit service is required')
  const expected = JSON.parse(execFileSync(process.env.EVOKIT_MCP_COMMAND,
    ['tools', '--profile', 'improver'], { encoding: 'utf8', timeout: 30000 }))
    .map(tool => `mcp__evokit__${tool.name}`).sort()
  const deadline = Date.now() + 30000
  const timer = setInterval(() => {
    const names = ctx.tools.definitions().map(tool => tool.name)
      .filter(name => name.startsWith('mcp__evokit__')).sort()
    if (JSON.stringify(names) === JSON.stringify(expected)) {
      console.log(JSON.stringify({ tools: names, modelRequests: 0 }))
      clearInterval(timer)
      exit(0)
    } else if (Date.now() >= deadline) {
      console.error(`Expected tools ${JSON.stringify(expected)}; found ${JSON.stringify(names)}`)
      clearInterval(timer)
      exit(1)
    }
  }, 100)
  ctx.effect(() => () => clearInterval(timer), 'evokit-list-tools.timer')
}
