/**
 * The sheet for the wall. One thing on it is not in the file: the address.
 *
 * It is printed under the code as words as well, because a camera that will
 * not focus, a cracked screen protector or somebody standing in the light is
 * the difference between a code and a queue at the door. The arena is the only
 * thing that knows what address a phone in this building can actually reach,
 * so it is asked rather than typed into the page.
 */

import { get } from "/static/api.js";

const address = document.getElementById("address");

show();

async function show() {
  try {
    const venue = await get("/api/venue");
    // Without the scheme: it is on a sheet for somebody to type, and nobody
    // types https:// into a phone.
    address.textContent = `${venue.public_url.replace(/^https?:\/\//, "")}/scan`;
  } catch {
    // A sheet with a blank line where the address goes is a sheet somebody
    // prints anyway. The code above it still works.
    address.hidden = true;
  }
}
