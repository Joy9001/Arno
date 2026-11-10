FROM postgres:18.6 AS pgclient

# Share the slim runtime base with the API; the sidecar needs clients, not a server.
FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic jq ca-certificates liblz4-1 libxxhash0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone/rclone:1.71.2 /usr/local/bin/rclone /usr/local/bin/rclone
COPY --from=pgclient /usr/lib/postgresql/18/bin/psql /usr/local/bin/psql
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libpq.so.5 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libgssapi_krb5.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libldap.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libsasl2.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkrb5.so.3 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libk5crypto.so.3 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkrb5support.so.0 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/liblber.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libkeyutils.so.1 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libcom_err.so.2 /usr/local/lib/
COPY --from=pgclient /usr/lib/x86_64-linux-gnu/libzstd.so.1 /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib

COPY backup/backup.sh /usr/local/bin/arno-backup
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/arno/
RUN chmod +x /usr/local/bin/arno-backup

ENTRYPOINT ["/usr/local/bin/arno-backup"]
