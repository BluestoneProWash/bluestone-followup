"""Bluestone Pro Wash - automated post-job follow-up engine.

This package is a pure decision engine. It does NOT talk to RevDek, Quo, or
Claude directly. A scheduled runner (see RUNBOOK.md) fetches data through the
RevDek integration, feeds it in here, and executes the actions this engine
returns. That keeps all the logic offline-testable.
"""

__all__ = ["config", "quotes", "window_plans", "templates", "timing", "classify", "store", "pipeline"]
