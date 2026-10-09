# Plans and Architecture Review

Apply these lenses to plans, proposals, ideas, architectures, migrations, and
technical decisions.

## Goal-to-Step Traceability

- Map every major step to the stated objective and success criteria.
- Identify objectives with no implementing step and steps with no objective.
- Check whether proposed metrics can establish the claimed outcome.

## Feasibility and Sequencing

- Verify prerequisites, dependencies, ownership, approvals, and lead times.
- Look for circular dependencies and steps that assume later work is complete.
- Test whether the timeline includes integration, migration, validation, and
  recovery work rather than implementation alone.

## Decision Quality

- Identify assumptions that determine whether the chosen approach wins.
- Check whether rejected alternatives were compared on the same criteria.
- Propose a discriminating experiment when uncertainty can be reduced cheaply.
- Do not report personal architectural preference as a defect.

## Migration and Transition States

- Review coexistence between old and new systems.
- Check data movement, dual writes, backfills, cutover, rollback, and version
  skew.
- Look for irreversible steps before validation gates.

## Operations and Ownership

- Identify who deploys, monitors, supports, approves, and reverses each phase.
- Check observability, alerting, runbooks, capacity, incident response, and
  maintenance burden.
- Verify that handoffs and decision gates have explicit owners.

## Cost and Capacity

- Examine demand, throughput, storage, staffing, vendor, and budget assumptions.
- Separate estimates from measured facts.
- Research current prices or product limits when they materially affect the
  decision.

## Premortem

Assume the plan failed and test plausible causes:

- a dependency was late or unavailable
- adoption or traffic differed from forecasts
- partial rollout created inconsistent states
- rollback was impossible or destructive
- ownership was unclear during an incident
- one local failure cascaded across systems or teams

Turn a premortem scenario into a finding only when the plan lacks a material
control and the consequence is concrete.

## Reversibility and Validation

- Check for staged rollout, stop conditions, rollback criteria, and decision
  checkpoints.
- Verify that success and failure can be observed before irreversible expansion.
- Prefer the smallest experiment that can disprove a critical assumption.
