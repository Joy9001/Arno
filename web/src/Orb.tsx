import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * An orb animates only while it can be seen: a hidden tab, a `display: none`
 * surface, or a scrolled-away hero spends no frames on the blur and spins.
 */
function useAnimated(enabled: boolean, ref: RefObject<HTMLSpanElement | null>) {
  const [inView, setInView] = useState(true);
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  useEffect(() => {
    if (!enabled) return;
    const onVisibility = () => setPageVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    let observer: IntersectionObserver | undefined;
    if (ref.current && typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver(([entry]) =>
        setInView(entry?.isIntersecting ?? true),
      );
      observer.observe(ref.current);
    }
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      observer?.disconnect();
    };
  }, [enabled, ref]);
  return enabled && inView && pageVisible;
}

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
  const ref = useRef<HTMLSpanElement>(null);
  const animates = useAnimated(live || thinking, ref);
  const classes = ["orb", animates ? "live" : "", thinking ? "thinking" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <span ref={ref} className={classes} style={{ ["--orb" as string]: `${size}px` }} aria-hidden="true">
      <i />
    </span>
  );
}
