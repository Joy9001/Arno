const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Money crosses the API as integer paise. This is display only. */
export function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "unknown";
  return money.format(paise / 100);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "unknown";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatMoment(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unknown";
  return `${Math.round(value * 100)}%`;
}
