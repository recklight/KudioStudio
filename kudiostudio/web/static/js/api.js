// Talking to the local server: commands, binary plot data, and events.
//
// The token is written into the page by the server; every request that
// reads or changes the session carries it.

const TOKEN = document.querySelector('meta[name="kudio-token"]').content;

export class CommandError extends Error {}

/** Run the Studio command *name*; resolves to its result. */
export async function call(name, args = [], kwargs = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Kudio-Token": TOKEN },
    body: JSON.stringify({ args, kwargs }),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new CommandError(`${name}: HTTP ${response.status}`);
  }
  if (!payload.ok) throw new CommandError(payload.error || `${name} failed`);
  return payload.result;
}

/** Binary data for a plot: the bytes and the X-Kudio-* headers that say
 *  what they are. Pass an AbortSignal to drop a request a newer one has
 *  made pointless. */
export async function data(path, params, signal) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(`/data/${path}?${query}`, {
    headers: { "X-Kudio-Token": TOKEN },
    signal,
  });
  if (!response.ok) {
    let why = `HTTP ${response.status}`;
    try { why = (await response.json()).error || why; } catch { /* binary */ }
    throw new CommandError(why);
  }
  const meta = {};
  for (const [key, value] of response.headers) {
    if (key.startsWith("x-kudio-")) meta[key.slice(8)] = value;
  }
  return { meta, buffer: await response.arrayBuffer() };
}

/** Listen to the server's events. *onEvent* gets each one; the stream
 *  reconnects by itself if the server is briefly away. */
export function listen(onEvent, onStatus = () => {}) {
  const source = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data));
    } catch (error) {
      console.error("bad event", error, message.data);
    }
  };
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  return source;
}
