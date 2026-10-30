// Talks to the studio server. The token comes once from the page address and is then
// dropped from it, so it does not stay in the history or a screenshot.
const token = new URLSearchParams(location.search).get("t") || "";
history.replaceState(null, "", location.pathname);

export class Superseded extends Error {}

async function call(method, path, body, signal) {
  const response = await fetch(path, {
    method,
    signal,
    headers: {
      "X-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 409) {
    const data = await response.json().catch(() => ({}));
    throw new Superseded(data.error || "superseded");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `${response.status}`);
  }
  return response;
}

function headerNumber(response, name) {
  const value = Number(response.headers.get(name));
  return Number.isFinite(value) ? value : null;
}

async function audio(response) {
  return {
    bytes: await response.arrayBuffer(),
    lufs: headerNumber(response, "X-Lufs"),
    originalLufs: headerNumber(response, "X-Original-Lufs"),
    ms: headerNumber(response, "X-Render-Ms"),
  };
}

export const api = {
  schema: async () => (await call("GET", "/api/schema")).json(),
  presets: async () => (await call("GET", "/api/presets")).json(),
  effects: async () => (await call("GET", "/api/effects")).json(),
  original: async () => audio(await call("GET", "/api/original")),
  references: async () => (await call("GET", "/api/references")).json(),
  reference: async (index) => audio(await call("GET", `/api/reference?i=${index}`)),
  referenceSpan: async (index, start, end) =>
    (await call("GET", `/api/reference_span?i=${index}&start=${start}&end=${end}`)).json(),
  preview: async (settings, start, end, seq, signal) =>
    audio(await call("POST", "/api/preview", { settings, start, end, seq }, signal)),
  render: async (settings, seq, signal) =>
    audio(await call("POST", "/api/render", { settings, seq }, signal)),
  save: async (settings) => (await call("POST", "/api/save", { settings })).json(),
  savePreset: async (name, settings) =>
    (await call("POST", "/api/preset", { name, settings })).json(),
};
