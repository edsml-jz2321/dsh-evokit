# Changelog

## v0.1.8

- Rewrite every MCP tool definition for agents: each description says what the
  tool does, when to use it and which sibling to use instead, its side effects
  and what it returns. Every parameter now has a description, and defaults are
  declared in the schemas. Tool behavior, names and accepted inputs are unchanged.
- Declare MCP tool annotations (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`); a test checks them against each tool's real
  effect on the workspace. The MCP server also publishes short instructions.
- Descriptions no longer start with a `[group]` tag; `evokit tools` still
  reports the group.
- Releases are automated: a `v*` tag publishes to PyPI, to npm through Trusted
  Publishing, to a GitHub Release with the changelog section, and to the official
  MCP Registry. Add `server.json` for the registry, kept at the package version.

## v0.1.7

- Accept every DSH 0.2.x from 0.2.2-rc.1: the bundle's `engines.dsh` range is
  now `>=0.2.2-rc.1 <0.3.0-0` instead of the exact `0.2.2-rc.1`. DSH matches
  ranges with prereleases included, so `<0.3.0-0` keeps 0.3.0 prereleases out.
  Install, listing, configuration dump and MCP tool and skill registration were checked on DSH
  0.2.2-rc.1 and 0.2.2-rc.4; the 0.1.6 bundle is refused on 0.2.2-rc.4.
- `EVOKIT_MCP_COMMAND`, `EVOKIT_WORKSPACE` and `EVOKIT_SKILL_ROOT` are optional and default
  to `evokit` on PATH, the directory where DSH starts and the bundle's own `skills` folder.
- The tool inspector overlay also checks that the `onboard`, `evolve` and
  `meta` skills are registered, and falls back to `evokit` on PATH.
- Lead both READMEs with what evokit does, how adoption is gated, the
  documented BFCL pilot result and the shortest install path. Add the MCP
  Registry ownership marker to the PyPI description.
- Add a `dsh-evokit` console command, identical to `evokit`, so `uvx dsh-evokit mcp`
  starts the MCP server.
- Add a root `package.json` declaring the same DSH bundle, so plugin catalogs and
  Git installs detect it; npm packages are still published from `integrations/dsh`.

## v0.1.6

- List the author as quei. Documentation and package metadata are English only.

- Ship the optional accounting relay in the Python wheel. Separate JSON
  byte admission from token context and conservatively reserve against both.
- Configure native DSH improver context at 524288 tokens with bounded 32768-token
  output; expose validated client-limit construction for other host profiles.
- Add periodic publication and bounded final publication retry, including real
  Windows deny-delete recovery without re-executing evaluations.
- Add operator-only circuit reset prerequisites and archived reset evidence;
  preserve reservations, settled cost and circuit thresholds.
- Add exactly-once prior-attempt cost/model/wall-time restoration. Missing wall
  time is explicitly a lower bound, never presented as complete latency.
- Add domain-validated completed-response caching with explicit stop metadata,
  revalidation of legacy records, and quarantined rejected/unvalidated replies.
  Transport remains responsible for settlement before content validation.

## v0.1.5

- Add generic paired-defined secondary comparisons, explicit both-undefined
  applicability, count guards, and complete-repeat quality noise estimates.
- Anchor configurable primary noise multipliers and optional observed-net floors;
  scale the reference for pooled repeat means. Gates over the mean of R>1 repeats
  now use threshold/sqrt(R), which is more permissive than v0.1.4. Single-repeat
  gates are unchanged; majority aggregation keeps the single-repeat threshold.
- Preserve reviewed v0.1.2/v0.1.3 default-noise epochs without rewriting their
  stored anchors. Repeat quality guards aggregate task means in task units.

## v0.1.4

- Select `deepseek-v4.1-flash` for proposals through a dedicated improver route,
  require served build `deepseek-v4-1-flash-261017`, and enable high thinking with
  a 32,768-token output budget. Preserve target and generator route settings.
  Account reasoning as a subset of output and remove DeepSeek cache controls.
- Strengthen the evolve playbook with measured detectability, full-diff literal review,
  record-cited history, predicted task flips and verified artifact load scope.
