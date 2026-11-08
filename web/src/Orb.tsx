/** The agent's presence. `live` animates it; use that only where the agent is present or working. */
export function Orb({
  size = 32,
  live = false,
  thinking = false,
  className = "",
}: {
  size?: number;
  live?: boolean;
  thinking?: boolean;
  className?: string;
}) {
  const classes = ["orb", live || thinking ? "live" : "", thinking ? "thinking" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes} style={{ ["--orb" as string]: `${size}px` }} aria-hidden="true">
      <i />
    </span>
  );
}
