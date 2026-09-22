"""Install the local Turbo performance layer before ``main.py`` is imported."""

import sys

# ``sitecustomize`` runs before Python resolves the script directory.  Add the
# pinned application's root explicitly so the installation can import its two
# patched modules during interpreter startup.
if "/MoneyPrinterTurbo" not in sys.path:
    sys.path.insert(0, "/MoneyPrinterTurbo")

from moneyprinter_accelerator import install

install()
