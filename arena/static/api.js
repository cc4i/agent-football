/**
 * Talking to the arena, and repeating what it said when it refuses.
 *
 * Every refusal the arena raises is written to be shown to a manager as-is,
 * so nothing here invents wording of its own except when the server sent
 * none at all.
 */

export class Refused extends Error {
  /**
   * `fields` is whichever of the request's fields the arena named, if it named
   * any. A form that has a box for one of them can say the refusal underneath
   * that box instead of in a banner above the whole page.
   */
  constructor(message, status, fields = []) {
    super(message);
    this.name = "Refused";
    this.status = status;
    this.fields = fields;
  }
}

export async function call(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // A phone that has wandered off the venue wifi, most likely.
    throw new Refused("Cannot reach the arena. Check the wifi and try again.", 0);
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Refused(reason(payload, response.status), response.status, blamed(payload));
  }
  return payload;
}

export const get = (path) => call("GET", path);
export const post = (path, body) => call("POST", path, body ?? {});

function reason(payload, status) {
  const detail = payload && payload.detail;
  // Through `sentences` like every other shape below it. The arena words its
  // refusals lower-case and unpunctuated, the way the rest of its messages are
  // written, and one that is about to be the only thing on a phone's screen
  // should still start like a sentence and end like one.
  if (typeof detail === "string") return sentences([detail]);
  if (Array.isArray(detail)) {
    // Pydantic hands back one entry per bad field. All of them are shown: a
    // form that reports its problems one at a time is a form you submit twice.
    const problems = detail.map((entry) => tidy(entry.msg)).filter(Boolean);
    if (problems.length) return sentences(problems);
  }
  if (detail && Array.isArray(detail.problems)) return sentences(detail.problems);
  return `The arena refused that (${status}).`;
}

/**
 * Which fields the refusal was about, in the order it named them.
 *
 * Pydantic locates each complaint as a path from the request down:
 * `["body", "email"]`, or `["query", "name"]`. The last step is the field,
 * which is what a form knows its boxes by. The arena's own refusals locate
 * themselves with `detail.field` when they are about one field in particular,
 * and name no field when they are about the request as a whole.
 */
function blamed(payload) {
  const detail = payload && payload.detail;
  // Arena's own located refusals: {detail: {problems: [...], field: "..."}}
  if (detail && typeof detail.field === "string") return [detail.field];
  // Pydantic's validation errors: [{loc: [...], msg: "..."}, ...]
  if (!Array.isArray(detail)) return [];
  const named = detail
    .map((entry) => (Array.isArray(entry.loc) && entry.loc.length > 1 ? entry.loc.at(-1) : null))
    .filter((field) => typeof field === "string");
  return [...new Set(named)];
}

function tidy(message) {
  // "Value error, that does not look like an email address" -> the useful half.
  return String(message || "").replace(/^Value error,\s*/, "");
}

function sentences(parts) {
  return parts
    .map((part) => (/[.!?]$/.test(part) ? part : `${part}.`))
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
