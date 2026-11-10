# Vendored: pgledger

Source: https://github.com/pgr0ss/pgledger

Pinned commit: `fee1aa05630f5af8cb3196749aa4d998162a9862` (2026-10-29)

Copied unmodified:

| Path | Upstream |
|---|---|
| `pgledger.sql` | `pgledger.sql` |
| `LICENSE` | `LICENSE` (MIT, © 2025 Paul Gross) |
| `pgsql-ulid/ulid-to-uuid.sql` | `vendor/scoville-pgsql-ulid/ulid-to-uuid.sql` |
| `pgsql-ulid/uuid-to-ulid.sql` | `vendor/scoville-pgsql-ulid/uuid-to-ulid.sql` |
| `pgsql-ulid/LICENSE` | `vendor/scoville-pgsql-ulid/LICENSE` (BSD-3-Clause, © 2021 Scoville) |
| `pgsql-ulid/README.md` | `vendor/scoville-pgsql-ulid/README.md` |

Migration `0001_pgledger.sql` includes these files in upstream's documented order
(`ulid-to-uuid.sql`, `uuid-to-ulid.sql`, `pgledger.sql`). Fin-Agent's own tables, views, and
grants live in later migrations; nothing in this directory is edited.

The license texts also ship in [`THIRD_PARTY_NOTICES.md`](../../../../../THIRD_PARTY_NOTICES.md).
