// Validate a DSH plugin index with DSH's own format-1 validator.
// Usage: copy this file next to a node_modules that contains
// @deepseek-ai/dsh-plugin-market-index, then run: node validate_market.mjs INDEX URL
import { readFileSync } from 'node:fs'
import { parseMarketIndex } from '@deepseek-ai/dsh-plugin-market-index'

const [file, url] = process.argv.slice(2)
const text = new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(file))
const index = parseMarketIndex(JSON.parse(text), url)
const versions = index.plugins.flatMap(plugin => plugin.versions.map(v => `${plugin.name}@${v.version}`))
console.log(`valid format-1 index: ${versions.join(', ')}`)
