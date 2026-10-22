"""HTTP route modules for the MoneyPrinter Cockpit gateway.

Each module owns an APIRouter and reaches the shared services in
``gateway.main`` through ``m`` so attribute lookups stay dynamic.
"""
