/** The product mark: two connected flows over one field, bundled locally as SVG. */
export function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg
      className="mark"
      width={size}
      height={size}
      viewBox="0 0 48 48"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="2" y="2" width="44" height="44" rx="14" fill="#2369d7" />
      <path
        d="M13 31c0-5.5 4.5-10 10-10h3c5 0 9-4 9-9"
        fill="none"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M13 38c0-5.5 4.5-10 10-10h3c5 0 9-4 9-9"
        fill="none"
        stroke="#fff"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle cx="13" cy="13" r="2.4" fill="#fff" />
    </svg>
  );
}
