# Hermes Agent with the doeedd finance skill, for the Railway service `hermes`.
# Build from the repository root: docker build -f deploy/hermes/Dockerfile .
#
# The official image keeps all state in the /opt/data volume (HERMES_HOME) and runs
# Hermes as user `hermes` under s6-overlay. This layer only adds OCR packages, the
# finance-agent code (read-only, outside the volume) and a boot hook.

# v0.22.5, pinned by digest so a re-tag upstream cannot change what we run.
FROM nousresearch/hermes-agent:v0.22.5@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d

USER root

# tesseract: OCR fallback for receipts the vision model cannot read (skill rules.md).
RUN apt-get -o Acquire::Retries=3 update \
 && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-ind tzdata \
 && rm -rf /var/lib/apt/lists/*

# Runtime needs only the standard library and httpx, which Hermes's venv already has.
COPY doeedd.py pyproject.toml /opt/finance-agent/
COPY doeedd_agent/ /opt/finance-agent/doeedd_agent/
COPY skills/ /opt/finance-agent/skills/
COPY deploy/hermes/doeedd_notify.py /opt/finance-agent/deploy/hermes/doeedd_notify.py
COPY --chmod=0755 deploy/hermes/cont-init.sh /etc/cont-init.d/50-finance-agent

ENV FINANCE_AGENT_DIR=/opt/finance-agent \
    TZ=Asia/Jakarta

# Fail the build early if the skill's interpreter cannot import the CLI.
RUN cd /opt/finance-agent && "$HERMES_PYTHON" doeedd.py --help >/dev/null

# Keep the image's s6 entrypoint; run the messaging gateway (Telegram) as the main
# process. Leave the Railway start command empty so this CMD is used.
CMD ["gateway", "run"]
