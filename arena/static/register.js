/**
 * The form behind the printed code: a name, an optional address, and that is
 * the whole of registering.
 *
 * No room is chosen here, because the sheet on the wall cannot name one. What
 * this earns somebody is a place on the board and a page of their own, and the
 * rooms they can walk into are on that page.
 *
 * The same page is where a manager who wants to be called something else comes
 * back to, so it answers to a phone the venue already knows: their name is in
 * the box and the button saves it rather than claiming it.
 */

import { get, Refused } from "/static/api.js";
import { signup } from "/static/signup.js";

const form = document.getElementById("register");
const problem = document.getElementById("problem");
const done = document.getElementById("done");
const name = document.getElementById("name");

// A join already on its way. `ready` holds the button down for the whole of
// it, including while the form clears the marks the last tap left.
let sending = false;

const who = signup({
  name,
  nameHint: document.getElementById("name-hint"),
  email: document.getElementById("email"),
  emailHint: document.getElementById("email-hint"),
  recoveryCode: document.getElementById("recovery-code"),
  recoveryHint: document.getElementById("recovery-hint"),
  changed: ready,
});

name.focus();
knownAlready();

async function knownAlready() {
  let me;
  try {
    me = await get("/api/players/me");
  } catch {
    // Nobody the venue knows, which is who this page is mostly for.
    return;
  }
  document.getElementById("step").textContent = "Change your name";
  document.querySelector(".ptitle").textContent = "Your name on the board";
  // The welcome underneath it is written for somebody who has never been here.
  // Somebody who has needs to know the one thing that worries them about
  // changing a name, which is whether it costs them what they have played for.
  document.querySelector(".psub").textContent =
    "This is what the big screen calls you. Change it and everything you have"
    + " done today comes with it.";
  done.textContent = "Save";
  name.value = me.display_name;
  name.select();
}

function ready() {
  done.disabled = sending || who.refused;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (done.disabled) return;
  problem.hidden = true;
  sending = true;
  ready();

  let player;
  try {
    player = await who.submit();
  } catch (failure) {
    if (!(failure instanceof Refused)) throw failure;
    problem.textContent = failure.message;
    problem.hidden = false;
    player = null;
  }
  if (!player) {
    sending = false;
    return ready();
  }
  // Show the recovery code in the page if they got one.
  if (player.recovery_code) {
    form.hidden = true;
    const show = document.createElement("div");
    show.className = "pscreen";

    const title = Object.assign(document.createElement("h1"), {
      className: "ptitle",
      textContent: "You're on the board"
    });
    const intro = Object.assign(document.createElement("p"), {
      className: "psub",
      textContent: "Your recovery code is how you come back on another phone."
    });
    const codeBox = document.createElement("div");
    codeBox.className = "recovery-code-display";
    const code = Object.assign(document.createElement("span"), {
      className: "mono code-large",
      textContent: player.recovery_code
    });
    codeBox.appendChild(code);
    const note = Object.assign(document.createElement("p"), {
      className: "psub",
      textContent: "You can see it any time on your home page."
    });
    const next = Object.assign(document.createElement("a"), {
      className: "btn primary push",
      href: "/home",
      textContent: "See the rooms"
    });

    show.append(title, intro, codeBox, note, next);
    document.body.appendChild(show);
  } else {
    location.assign("/home");
  }
});
