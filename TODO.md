# To do

## Catch failed runs before they cost hours

Aim: a broken run dies in minutes, not at hour 5, and the same mistake never happens twice.
Built from the first real failures (campaign "PLA-PCL water uptake", 2026-09-24): two runs hit one script
bug (asking a periodic cell for its atoms), one blew up in dynamics (energy exploded between steps).

1. **Smoke run before every real run.** For every script the MCP also writes a tiny version: the same code
   path with a small cell, a few steps and a short time budget (about 2 minutes on the VM). The full script
   is only given out once the smoke run's bundle comes back clean. Smoke bundles are marked as smoke and
   kept out of the models, ghosts and blind tests. This catches script bugs, which the stand-in BIOVIA of
   `milo_check_script` cannot, since it accepts any call.
2. **Campaign pilot.** Before a campaign, smoke-test its extreme knob values (the edges of the range break
   first). One shared bug then stops the whole campaign at minute 2.
3. **Fail-fast guards in the scripts.** Stages run cheapest first (build, minimize, short dynamics,
   production). The scripts check the minimized structure's energy and forces before dynamics, and energy
   and temperature over the first minutes of dynamics. If a check fails, the run stops with a clear error
   in its bundle.
4. **Checkpoints.** The structure is saved after each stage, so a redo (`redo_of`) can start from the
   last good stage instead of from scratch.
5. **Live progress.** A running script writes a small progress file into the drop folder: its stage,
   latest energy and when it last reported. MILO shows running runs, and flags one that stalls or dies.
6. **Lessons from every failure.** Each failure is kept as a lesson (error, cause, fix). The MCP checks
   new scripts against the lessons before handing them out.
7. **Borrow from existing tools.** AiiDA (restarts driven by error handlers), Custodian (Materials
   Project: watches running jobs and fixes known errors) and FireWorks (workflow reruns) show the pattern:
   watch the job, recognize known errors, fix or restart. None of them runs Materials Studio, so the ideas
   are copied, not the tools.

Build order: 1 and 3 first (together they would have caught all three failures within minutes), then 2,
4, 6, 5.
