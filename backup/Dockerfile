FROM postgres:18.6

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backup/backup.sh /usr/local/bin/fin-agent-backup
COPY THIRD_PARTY_NOTICES.md /usr/share/doc/fin-agent/THIRD_PARTY_NOTICES.md
RUN chmod +x /usr/local/bin/fin-agent-backup

ENTRYPOINT ["/usr/local/bin/fin-agent-backup"]
