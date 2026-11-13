# Changelog

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
