"""Fixture module that fails on import; scanned only by the failure test."""

raise RuntimeError("kaboom: fixture module exploded on import")
