export function formatRupees(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "no data";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: paise % 100 === 0 ? 0 : 2,
  }).format(paise / 100);
}
