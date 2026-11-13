"""EVIDENCE FACTORY (Drosophila's evolution engine): invents new, orthogonal 'what-if' voters, judges them strictly, and stages the
survivors as single-voter Shadow Arbiter upgrades.
  dsl.py        strictly causal expression language (AST as JSON lists) + compiler + causality probe
  archive.py    MAP-Elites niche archive (trade frequency x hold time x BTC correlation, per side) + orthogonality filter
  generator.py  genetic programming (subtree mutation / crossover) + LLM hook that writes veto filters from clusters of losing trades
  guard.py      multiple-testing guardrail: deflated t threshold from the cumulative number of trials
  stage.py      stage_arbiter_upgrade(): an elite becomes an extra Arbiter voter, scored live as a challenger clone
  run.py        the nightly loop (time-budgeted), state kept on the 'evo' branch"""
