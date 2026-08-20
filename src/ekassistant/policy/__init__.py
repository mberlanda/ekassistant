"""Identity, Delegation and Policy: what a caller is allowed to ask for.

See docs/design/policy.md. Kept separate from `identity/` (which answers
"who is this and what groups are they in") because group membership and
policy change on different clocks and are owned by different people -
conflating them is how a directory edit silently becomes a policy change.
"""
