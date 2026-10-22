"""Domain services for the MoneyPrinter Cockpit gateway.

Services are plain functions grouped by domain. Every reference to state, config,
other services or routes goes through the `m` alias (gateway.main), resolved at
call time, so runtime overrides from tests and workers keep working. Import
gateway.main, not these modules directly.
"""
