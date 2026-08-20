"""Capabilities and Effects: typed tools, the registry, and the gateway.

See docs/design/capabilities.md. This subpackage owns the answer to "what
is this system allowed to do, on whose authority, and with what
consequence" - deliberately separate from `models/` (which owns what the
LLM says) because tool authority must never be inferable from model
output.
"""
