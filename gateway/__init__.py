"""MoneyPrint Cockpit Gateway package.

Keep this module free of imports. The Temporal worker loads workflow code inside a
sandbox, and importing the API here pulls httpx into that sandbox.
"""
