/**
 * Numbers as the pages say them out loud.
 *
 * Both the results screen and a manager's own page tell somebody where they
 * came, and the board and both of those print a total. Three copies of "3rd"
 * is three chances for one of them to say "3th".
 */

/** A place: 1st, 2nd, 3rd, 4th - and 11th, 12th, 13th. */
export function ordinal(place) {
  // The teens, which the last-digit rule below gets wrong.
  const teens = place % 100;
  if (teens >= 11 && teens <= 13) return `${place}th`;
  return `${place}${["th", "st", "nd", "rd"][place % 10] || "th"}`;
}

/** A total, grouped: 12,400 rather than 12400. */
export const figure = (points) => points.toLocaleString("en-GB");
