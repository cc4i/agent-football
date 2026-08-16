/**
 * Sockets that survive a phone going to sleep.
 *
 * A venue's wifi drops, a handset backgrounds itself, a laptop lid closes. The
 * arena's log is gapless and every client re-reads what it is watching on
 * connect, so reconnecting is always safe -- which makes giving up the wrong
 * default.
 */

const FIRST_WAIT_MS = 500;
const LONGEST_WAIT_MS = 8000;

export function openRoom(code, { clientId = "", ...handlers } = {}) {
  const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  return open(`/ws/rooms/${encodeURIComponent(code)}${query}`, handlers);
}

/**
 * The screen token for a room this tab opened, and where it is kept.
 *
 * Whoever opened a room holds the one token that says so, and a socket
 * carrying it is what tells the arena somebody is still behind the lobby --
 * without which the sweep gives up on it in HOST_GONE_SECONDS. That used to be
 * the big screen's business alone and lived privately in `arena.js`. A phone
 * can open a room now, so three pages need the same spelling of the key and
 * none of them should be inventing it.
 *
 * sessionStorage rather than localStorage, which is the part worth keeping
 * deliberate: the token dies with the tab. A lobby whose opener closed the
 * page stops being vouched for, and the sweep clears it rather than leaving a
 * room in the venue's list that nobody is behind.
 */
const tokenKey = (code) => `arena.screen.${code}`;

export const screenToken = (code) => sessionStorage.getItem(tokenKey(code)) || "";

export const keepScreenToken = (code, token) =>
  sessionStorage.setItem(tokenKey(code), token);

export function openWall(handlers = {}) {
  return open("/ws/wall", handlers);
}

function open(path, { onMessage, onOpen, onDrop } = {}) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const address = `${scheme}://${location.host}${path}`;

  let socket = null;
  let wait = FIRST_WAIT_MS;
  let retry = null;
  let closed = false;

  function connect() {
    socket = new WebSocket(address);
    socket.addEventListener("open", () => {
      wait = FIRST_WAIT_MS;
      if (onOpen) onOpen();
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (onMessage) onMessage(message);
    });
    socket.addEventListener("close", (event) => {
      if (closed) return;
      // 4404 is the arena saying this room does not exist. Retrying that would
      // hammer the server forever over a mistyped code.
      if (event.code === 4404) {
        closed = true;
        if (onDrop) onDrop(event.reason || "there is no such room", true);
        return;
      }
      if (onDrop) onDrop(event.reason || "reconnecting", false);
      retry = setTimeout(connect, wait);
      wait = Math.min(wait * 2, LONGEST_WAIT_MS);
    });
  }

  connect();

  return {
    send(message) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
        return true;
      }
      return false;
    },
    close() {
      closed = true;
      clearTimeout(retry);
      if (!socket) return;
      // A socket closed mid-handshake is a failed connection as far as the
      // browser is concerned, and it says so in the console. The wall cuts
      // between matches faster than a handshake finishes, so left alone that
      // is a warning every few seconds on a screen that runs all evening --
      // and an arena accepting connections nobody is on the other end of.
      // Nothing is sent in the meantime: this hangs up the moment it can.
      if (socket.readyState === WebSocket.CONNECTING) {
        socket.addEventListener("open", (event) => event.target.close());
        return;
      }
      socket.close();
    },
  };
}
