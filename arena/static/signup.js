/**
 * The two boxes that decide who somebody is: a name, and an address they may
 * keep to themselves.
 *
 * Two pages ask for them. The sheet on the wall sends a stranger to /register,
 * and the code beside a screen sends one straight to /join, where the room is
 * already decided. Asking the same two questions twice is fine; asking them
 * differently is not, and it is not the wording that drifts. It is when the
 * name is checked, which refusal is said under which box, and whether a taken
 * name stops the button. All of that lives here so that there is one of it.
 *
 * The name is the one thing on either page that somebody else can already have
 * taken, so the arena is asked about it while it is being typed rather than
 * only when the button is tapped. The tap is still what decides - two phones
 * can type the same name in the same second - and it says the same sentence.
 */

import { get, post, Refused } from "/static/api.js";

// How long a phone keyboard goes quiet before the name is worth asking about.
// Long enough that typing a name is one question rather than a dozen, short
// enough that the answer is there before a thumb reaches the button.
const TYPING_PAUSE = 400;

/**
 * Wire up a page's name and address boxes.
 *
 * `changed` is called whenever something happened that could move the button
 * between enabled and disabled, because only the page knows what else it is
 * waiting for: a free seat, a stance, a request already in flight.
 */
export function signup({ name, nameHint, email, emailHint, changed = () => {} }) {
  // Only a name the arena has actually refused blocks the button. Somewhere
  // between a keystroke and an answer it is unknown, and a form that will not
  // be submitted until a network call comes back is a form a bad wifi locks.
  let refused = false;
  let asking = 0;
  let pause = null;

  name.addEventListener("input", () => {
    // Whatever the arena last said was about a name no longer in the box.
    aboutTheName(null);
    clearTimeout(pause);
    pause = setTimeout(askAboutTheName, TYPING_PAUSE);
  });

  email.addEventListener("input", () => aboutTheAddress(null));

  async function askAboutTheName() {
    const typed = name.value.trim();
    if (!typed) return;
    const question = ++asking;
    let answer;
    try {
      answer = await get(`/api/players/available?name=${encodeURIComponent(typed)}`);
    } catch {
      // The join will say so for itself if the name really is taken. A phone
      // that cannot reach the arena, or a name the check will not answer about
      // at all, must not be reported to a manager as somebody else's.
      return;
    }
    // A later keystroke has already overtaken this one, so its answer is about
    // a name that is no longer in the box.
    if (question !== asking) return;
    aboutTheName(answer.available ? null : `${answer.name} is taken. Try another name.`);
  }

  function aboutTheName(trouble) {
    refused = Boolean(trouble);
    mark(name, nameHint, trouble);
    changed();
  }

  function aboutTheAddress(trouble) {
    // Nothing here blocks the button the way a taken name does. The arena is
    // the only judge of what an address looks like, so the only way to find out
    // whether the next thing typed suits it is to send it.
    mark(email, emailHint, trouble);
  }

  function mark(box, hint, trouble) {
    hint.textContent = trouble || "";
    hint.hidden = !trouble;
    box.classList.toggle("wrong", Boolean(trouble));
  }

  /**
   * A refusal, said under the box that has to change if there is one.
   *
   * A 409 is the name every time: it is the only thing on either form that
   * somebody else can be holding. A 422 names its own fields, and the arena's
   * two are both boxes here - but only a refusal about exactly one of them can
   * go under a box, because the sentence covers every problem it found and
   * half of it under each would be two wrong sentences. The rest is thrown for
   * the page's banner: a phone off the wifi is about neither box.
   */
  const BOXES = { display_name: aboutTheName, email: aboutTheAddress };

  function sayWhereItBelongs(failure) {
    if (!(failure instanceof Refused)) throw failure;
    if (failure.status === 409) return aboutTheName(failure.message);
    const boxes = failure.fields.map((field) => BOXES[field]).filter(Boolean);
    if (boxes.length === 1 && boxes.length === failure.fields.length) {
      return boxes[0](failure.message);
    }
    throw failure;
  }

  return {
    /** True while the arena is holding a name against this form. */
    get refused() {
      return refused;
    },

    /**
     * Become a player, or say why not under the box it is about.
     *
     * Returns the player. Returns null when the refusal has been said under a
     * box and there is nothing further for the page to do about it. Throws the
     * rest, for the page's banner.
     */
    async submit() {
      // Everything the last tap was told is about to be answered again.
      aboutTheName(null);
      aboutTheAddress(null);
      // Nothing is going to answer about a name that is already being sent.
      clearTimeout(pause);
      try {
        return await post("/api/players", {
          display_name: name.value.trim(),
          email: email.value.trim(),
        });
      } catch (failure) {
        sayWhereItBelongs(failure);
        return null;
      }
    },
  };
}
