/** Camera and colours for the 3D map; kept apart from the WebGL view so importing them does not
 * pull three.js into the main bundle. */

export interface Camera { azimuth: number; elevation: number; distance: number }

export interface Palette { bg: string; ink: string; muted: string; line: string; panel: string; heat: string; kelly: string; s1: string; s3: string }

/** Read the theme's colours so the 3D view follows light and dark mode. */
export function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return {
    bg: v("--sunk", "#f4f8f1"), ink: v("--ink", "#13210f"), muted: v("--muted", "#56634f"), line: v("--line-strong", "#c3cfbb"),
    panel: v("--panel", "#ffffff"), heat: v("--heat", "#0a4a20"), kelly: v("--kelly", "#4cbb17"), s1: v("--s1", "#0e4d26"), s3: v("--s3", "#52b266"),
  };
}
