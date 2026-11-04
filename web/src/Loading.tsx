import { ClipLoader, PulseLoader } from "react-spinners";

/** A panel's first load. The label says what is loading; the spinner says it is moving. */
export function Loading({ label }: { label: string }) {
  return (
    <p className="loading" role="status">
      <ClipLoader size={14} color="currentColor" speedMultiplier={0.85} />
      <span>{label}</span>
    </p>
  );
}

/** The spinner inside a button whose label already changed to Saving…, Opening…, and so on. */
export function Busy() {
  return (
    <span className="busy-dot" aria-hidden="true">
      <ClipLoader size={12} color="currentColor" speedMultiplier={0.85} />
    </span>
  );
}

/** Three dots for a turn that is still streaming, where a circle reads as a page load. */
export function Typing() {
  return (
    <span className="busy-dot" aria-hidden="true">
      <PulseLoader size={5} color="currentColor" margin={3} speedMultiplier={0.9} />
    </span>
  );
}
