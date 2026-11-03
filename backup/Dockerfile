FROM postgres:18.6

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone/rclone:1.71.2 /usr/local/bin/rclone /usr/local/bin/rclone

COPY backup/backup.sh /usr/local/bin/fin-agent-backup
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/fin-agent/
RUN chmod +x /usr/local/bin/fin-agent-backup

ENTRYPOINT ["/usr/local/bin/fin-agent-backup"]
