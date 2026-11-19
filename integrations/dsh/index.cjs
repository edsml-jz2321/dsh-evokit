'use strict';

// dsh-evokit is a configuration-only DeepSeek Harness bundle. DSH applies
// cordis.patch.yml, which wires the evokit MCP server (the Python engine from
// PyPI: `uv tool install dsh-evokit`) and the onboard, evolve and meta skills
// into a profile. No patch row references this module, so DSH never loads it;
// it only gives the package a resolvable entry that describes the bundle.
const path = require('node:path');

module.exports = {
  name: 'dsh-evokit',
  kind: 'configuration-only DSH bundle',
  patch: path.join(__dirname, 'cordis.patch.yml'),
  mcpCommand: ['evokit', 'mcp'],
};
