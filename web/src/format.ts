const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const moneyWhole = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

/** Money crosses the API as integer paise. This is display only. */
export function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "—";
  return paise % 100 === 0 ? moneyWhole.format(paise / 100) : money.format(paise / 100);
}

/** A local calendar day as yyyy-mm-dd. `toISOString()` would shift the date west of UTC. */
export function isoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatMoment(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

/** The weekday and time only, for a moment close at hand: "Tue 9:00 am". */
export function formatMomentShort(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** "in 3 h", "5 min ago", or "just now". Round numbers only: this is a hint, not a clock. */
export function formatRelative(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.round((date.getTime() - now) / 1000);
  if (Math.abs(seconds) < 45) return "just now";
  const future = seconds > 0;
  const size = Math.abs(seconds);
  const text =
    size < 3600
      ? `${Math.round(size / 60)} min`
      : size < 86_400
        ? `${Math.round(size / 3600)} h`
        : `${Math.round(size / 86_400)} d`;
  return future ? `in ${text}` : `${text} ago`;
}

/** A short elapsed span: "42s", "1m 12s", "2h 5m". */
export function formatDuration(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.round(seconds)) : 0;
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    const rest = total % 60;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}
