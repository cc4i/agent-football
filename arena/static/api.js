/**
 * Talking to the arena, and repeating what it said when it refuses.
 *
 * Every refusal the arena raises is written to be shown to a manager as-is,
 * so nothing here invents wording of its own except when the server sent
 * none at all.
 */

export class Refused extends Error {
  constructor(message, status) {
    super(message);
    this.name = "Refused";
    this.status = status;
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
  if (!response.ok) throw new Refused(reason(payload, response.status), response.status);
  return payload;
}

export const get = (path) => call("GET", path);
export const post = (path, body) => call("POST", path, body ?? {});

function reason(payload, status) {
  const detail = payload && payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // Pydantic hands back one entry per bad field. All of them are shown: a
    // form that reports its problems one at a time is a form you submit twice.
    const problems = detail.map((entry) => tidy(entry.msg)).filter(Boolean);
    if (problems.length) return sentences(problems);
  }
  if (detail && Array.isArray(detail.problems)) return sentences(detail.problems);
  return `The arena refused that (${status}).`;
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
