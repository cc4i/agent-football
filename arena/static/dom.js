/**
 * The scraps of markup more than one page builds by hand.
 */

/**
 * An emoji as its own element, so the gap beside the label belongs to CSS.
 * A literal space between a colour emoji and 'Outfit' sets far too tight to
 * read as a separator.
 */
export function icon(glyph) {
  const mark = document.createElement("span");
  mark.className = "ico";
  mark.textContent = glyph;
  // A bare emoji is announced by its CLDR name, which only repeats the label
  // sitting next to it.
  mark.setAttribute("aria-hidden", "true");
  return mark;
}
