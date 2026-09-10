/**
 * Stable decoration derived from stable strings.
 *
 * The reference aesthetic wants every mode tile to carry its own colour and
 * glyph, and every player to carry a face. Neither is in the wire schema and
 * neither should be: modes are rows (spec §06), so hard-coding a palette by
 * mode id would mean a deploy every time someone runs an INSERT.
 *
 * So decoration is a pure function of the identifier. Same slug, same hue,
 * every render and every client — no state, no server round-trip, and unit
 * tests instead of eyeballing.
 */

/** FNV-1a, 32-bit. Cheap, well-mixed on short ASCII, and identical here and
 * anywhere else that ever needs the same colour for the same slug. */
export function hashString(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** The golden angle. Successive multiples of it land as far apart on the hue
 * wheel as a fixed step can, which is the whole trick behind a grid of tiles
 * that reads as many colours rather than one. */
const GOLDEN_ANGLE = 137.5;

/**
 * A hue in [0, 360) for the nth tile in a list.
 *
 * Position, not identity, on purpose. Hashing a slug to a hue looks more
 * principled and is worse: five slugs into any small bucket space collide
 * often enough to put two identically-tinted tiles side by side, and a hash
 * cannot be made to promise otherwise. Stepping by position guarantees that
 * whatever is on screen together is distinct — the property that actually
 * matters — and every client renders the same list in the same order, so the
 * colour is stable across players anyway.
 */
export function hueForIndex(index: number): number {
  return ((index * GOLDEN_ANGLE) % 360 + 360) % 360;
}

/** Glyphs used when a slug says nothing recognisable. */
const FALLBACK_ICONS = ["🎌", "🌸", "⭐", "🎴", "🍥", "🗾", "🎧", "📺"];

/** Slug/name keywords worth recognising. First match wins, so order is
 * specificity: "op" would match half the dictionary if it came first. */
const ICON_KEYWORDS: ReadonlyArray<readonly [RegExp, string]> = [
  // Genres first, and each one distinct from its neighbours: shoujo and
  // romance sit side by side on the home grid, so they must not both be a
  // pink heart. Same for isekai and fantasy.
  [/shonen|shounen/, "⚔️"],
  [/shoujo|shojo/, "🎀"],
  [/seinen/, "🌑"],
  [/slice.?of.?life|iyashikei|comfy|ghibli/, "🌿"],
  [/romance|love/, "💞"],
  [/isekai/, "🪄"],
  [/comedy|gag|funny/, "😂"],
  [/fantasy|magic/, "🐉"],
  // Then everything else a slug might plausibly say.
  [/quick|speed|blitz|rapid|sprint/, "⚡"],
  [/hard|expert|brutal|nightmare|insane/, "🔥"],
  [/easy|starter|beginner/, "🌱"],
  [/battle|fight|tournament/, "👊"],
  [/mecha|robot|gundam/, "🤖"],
  [/movie|film|cinema/, "🎬"],
  [/music|opening|ending|soundtrack|theme/, "🎵"],
  [/horror|demon|curse/, "👻"],
  [/sport|volley|basket|soccer/, "🏐"],
  [/food|cook|gourmet/, "🍜"],
  [/classic|retro|golden|nostalgi/, "📼"],
  [/season|current|airing|new/, "🗓️"],
  [/pirate|adventure|journey/, "🧭"],
];

/** The tile glyph for a game mode. Keyword-matched where the slug is telling,
 * hashed where it is not — never blank. */
export function iconForMode(slug: string, name = ""): string {
  const haystack = `${slug} ${name}`.toLowerCase();
  for (const [pattern, icon] of ICON_KEYWORDS) {
    if (pattern.test(haystack)) return icon;
  }
  return FALLBACK_ICONS[hashString(slug) % FALLBACK_ICONS.length]!;
}

/** Faces for the roster. Deliberately expression-forward rather than
 * person-shaped: an avatar that suggests a specific human would be a claim
 * about a player the server never made. */
const AVATARS = [
  "😎", "🦊", "👾", "🐱", "🍥", "🐼", "🦉", "🐙",
  "🌟", "🍡", "🐧", "🦖", "🎧", "🐸", "🦄", "🌙",
];

/** The same player gets the same face for as long as their id lives — which
 * is exactly one session, and that is the intended lifetime. */
export function avatarFor(key: string): string {
  return AVATARS[hashString(key) % AVATARS.length]!;
}
