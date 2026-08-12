/**
 * A room socket that survives a phone going to sleep.
 *
 * A venue's wifi drops, a handset backgrounds itself, a laptop lid closes. The
 * arena's log is gapless and every client re-reads the room on connect, so
 * reconnecting is always safe -- which makes giving up the wrong default.
 */

const FIRST_WAIT_MS = 500;
const LONGEST_WAIT_MS = 8000;

export function openRoom(code, { clientId = "", onMessage, onOpen, onDrop } = {}) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  const address = `${scheme}://${location.host}/ws/rooms/${encodeURIComponent(code)}${query}`;

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
      if (socket) socket.close();
    },
  };
}
