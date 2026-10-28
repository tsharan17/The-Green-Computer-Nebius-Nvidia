/** The mark: a chip outline whose die is a leaf-shaped heat contour. Inline SVG, so it renders
 * offline and in any font. */
export default function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg className="logo-svg" width={size} height={size} viewBox="0 0 32 32" aria-hidden focusable="false">
      <rect x="1" y="1" width="30" height="30" rx="7" fill="var(--accent)" />
      <g stroke="var(--accent-ink)" strokeWidth="1.6" strokeLinecap="round" opacity="0.9">
        <path d="M11 6v3M16 6v3M21 6v3M11 23v3M16 23v3M21 23v3M6 11h3M6 16h3M6 21h3M23 11h3M23 16h3M23 21h3" />
      </g>
      <rect x="9" y="9" width="14" height="14" rx="2.5" fill="none" stroke="var(--accent-ink)" strokeWidth="1.6" />
      <path d="M12 20c0-5 3-8 8-8 0 5-3 8-8 8z" fill="var(--accent-ink)" />
    </svg>
  );
}
