# On-Call Runbook

## Escalation policy

If a production alert fires outside business hours, the primary on-call
engineer has 15 minutes to acknowledge it before it pages the secondary.
Acknowledge in the incident tool, not just in chat - chat acknowledgments
are not tracked and do not stop the page.

## Restarting the deploy pipeline

If the deploy pipeline is stuck, re-run the failed stage from the CI
dashboard rather than pushing an empty commit. Re-running preserves the
original commit's test results; a new commit resets them and hides
whether the failure was flaky or real.

## Database failover

Manual failover to the replica is only authorized for Sev1 incidents and
requires a second engineer to confirm the decision in the incident
channel before running the failover script.
