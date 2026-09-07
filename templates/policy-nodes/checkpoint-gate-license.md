---
id: policy/checkpoint-gate-license
type: policy
context: {{CONTEXT}}
summary: When the checkpoint-gate knob's window reaches its declared sample size, file the per-class stance decision; without this node the evaluator degrades to shadow (H-241).
trigger: event/checkpoint-gate-threshold-reached
enforcement: procedural
then: [command/file-gate-decision]
status: current
---
The licensing node the H-241 rule requires: an observer acts only when a committed policy node
names the event in `trigger:` with a resolvable `then:`. `knob-observe.py evaluate
checkpoint-gate-stance` looks for exactly this trigger and resolves each `then:` entry to a
command node file under the model directory; if the node is absent or its `then:` does not
resolve, recommend mode degrades to shadow behavior (would_set recorded, nothing filed) and the
state row names the missing license.
