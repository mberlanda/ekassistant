"""Mock capabilities - one per zone, so every seam has something behind it.

These are fixtures, not stubs: each implements the real Capability
protocol, declares a real contract and enforces the invariants its real
counterpart will (unknown account raises, unknown metric refuses, send is
idempotent and kill-switchable). Writing later phases against a permissive
stub would mean discovering the actual semantics at integration time.

Registered into a CapabilityRegistry by capabilities/bootstrap.py.
"""
