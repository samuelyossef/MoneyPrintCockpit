FROM ghcr.io/harry0703/moneyprinterturbo:v1.3.7@sha256:999fbf91934e763a6405b4aecce9a2acaa84c76d0dcae39cf2cf170e9d530889

# This isolated build context deliberately contains no runtime configuration or
# provider credentials.  The existing read-only config mount remains the only
# place where those values are available.
COPY sitecustomize.py /opt/moneyprinter-accelerator/sitecustomize.py
COPY moneyprinter_accelerator.py /opt/moneyprinter-accelerator/moneyprinter_accelerator.py
ENV PYTHONPATH=/opt/moneyprinter-accelerator
