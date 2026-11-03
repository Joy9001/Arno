#!/bin/sh
# Fin-Agent backup sidecar.
#
# Checks hourly. If the last successful backup is over MAX_AGE old, it dumps the app database,
# hands the dump to restic, prunes old snapshots, and records the attempt in Postgres through the
# `backup` role (SELECT everywhere, INSERT into backup_runs only).
set -eu
umask 077

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"

INTERVAL="${BACKUP_INTERVAL_SECONDS:-3600}"
MAX_AGE="${BACKUP_MAX_AGE_SECONDS:-86400}"
DUMP="${BACKUP_DUMP_PATH:-/tmp/fin_agent.dump}"
MANIFEST="${BACKUP_MANIFEST_PATH:-/tmp/backup_manifest.json}"

log() {
    printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

last_success_age() {
    psql "$DATABASE_URL" -Atq -v ON_ERROR_STOP=1 -c \
        "SELECT coalesce(extract(epoch from (now() - max(finished_at)))::bigint, 999999999) FROM backup_runs WHERE status = 'success'"
}

record() {
    record_status="$1"
    record_started="$2"
    record_finished="$3"
    record_bytes="$4"
    record_duration="$5"
    record_snapshot="$6"
    record_error="$7"
    psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 \
        -v status="$record_status" \
        -v started="$record_started" \
        -v finished="$record_finished" \
        -v bytes="$record_bytes" \
        -v duration="$record_duration" \
        -v snapshot="$record_snapshot" \
        -v error="$record_error" <<'SQL'
INSERT INTO backup_runs
    (started_at, finished_at, status, dump_bytes, duration_ms, restic_snapshot, error)
VALUES
    (:'started'::timestamptz, :'finished'::timestamptz, :'status',
     NULLIF(:'bytes', '')::bigint, NULLIF(:'duration', '')::bigint,
     NULLIF(:'snapshot', ''), NULLIF(:'error', ''));
SQL
}

run_backup() {
    started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    start_epoch=$(date +%s)
    log "starting pg_dump"

    if ! pg_dump --no-owner --no-privileges --format=custom --file="$DUMP" "$DATABASE_URL"; then
        finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        duration=$(( $(date +%s) - start_epoch ))
        log "pg_dump failed"
        record failed "$started" "$finished" "" "$((duration * 1000))" "" "pg_dump failed" || true
        return 1
    fi

    bytes=$(stat -c%s "$DUMP")
    dump_sha=$(sha256sum "$DUMP" | cut -d' ' -f1)

    # The restore drill compares this manifest with the restored database.
    # TODO(backup): the manifest is generated immediately after pg_dump, so a write that lands
    # between the two can make a restore report a mismatch. Use pg_export_snapshot() for an exact
    # point-in-time manifest if this becomes noisy in practice.
    if ! psql "$DATABASE_URL" -Atq -v ON_ERROR_STOP=1 \
        -c "SELECT jsonb_pretty(fin_agent_backup_manifest())" > "$MANIFEST"; then
        finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        duration=$(( $(date +%s) - start_epoch ))
        log "backup manifest failed"
        record failed "$started" "$finished" "$bytes" "$((duration * 1000))" "" "manifest failed" || true
        rm -f "$DUMP" "$MANIFEST"
        return 1
    fi
    jq --arg sha "$dump_sha" '. + {dump_sha256: $sha}' "$MANIFEST" > "$MANIFEST.tmp" \
        && mv "$MANIFEST.tmp" "$MANIFEST"

    if ! restic snapshots --json >/dev/null 2>&1; then
        log "restic repository is not initialised; running restic init"
        if ! restic init; then
            finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
            duration=$(( $(date +%s) - start_epoch ))
            log "restic init failed"
            record failed "$started" "$finished" "$bytes" "$((duration * 1000))" "" "restic init failed" || true
            rm -f "$DUMP" "$MANIFEST"
            return 1
        fi
    fi

    if summary=$(restic backup --json --tag fin-agent --host fin-agent "$DUMP" "$MANIFEST"); then
        snapshot=$(printf '%s' "$summary" | jq -r 'select(.message_type == "summary") | .snapshot_id' | tail -n 1)
        restic forget --keep-daily 7 --keep-weekly 4 --prune >/dev/null 2>&1 || true
        finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        duration=$(( $(date +%s) - start_epoch ))
        log "success: ${bytes} bytes, snapshot ${snapshot:-unknown}"
        record success "$started" "$finished" "$bytes" "$((duration * 1000))" "$snapshot" "" || true
        rm -f "$DUMP" "$MANIFEST"
        return 0
    fi

    finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    duration=$(( $(date +%s) - start_epoch ))
    log "restic backup failed"
    record failed "$started" "$finished" "$bytes" "$((duration * 1000))" "" "restic backup failed" || true
    rm -f "$DUMP" "$MANIFEST"
    return 1
}

while true; do
    age=$(last_success_age 2>/dev/null || printf '999999999')
    if [ "$age" -lt "$MAX_AGE" ]; then
        log "last success ${age}s ago; next check in ${INTERVAL}s"
    else
        log "last success ${age}s ago; backup is due"
        run_backup || true
    fi
    sleep "$INTERVAL"
done
