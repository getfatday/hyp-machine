---
bump: patch
---
Every plugin SessionStart hook now has a 60 s timeout (was 10 to 20 s). Under normal host load on a lab-sized repository the six hooks run concurrently and the 10 s ones were cancelled at their limit, which silently drops the hook's context line from the session (observed: compile-dashboard --check cancelled at 10.1 s, drift-check and the session resolver cancelled at 10 to 15 s when the host was busy). With 60 s no SessionStart hook was cancelled in any measured session while total SessionStart wall stayed under 45 s; nothing else in hooks.json changes. Lab H-DRAFT-d8609270-plugin-hook-timeout-margin, kept 5/5 in two consecutive runs (the second cold, whose OFF arm reproduced a cancellation at load 12); consumer gap G10 of the lab-plugin convergence program.
