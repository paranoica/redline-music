// The song page: the open song's screen, or the list of songs when none is open or when the
// song's name in the top bar is pressed.

import { token } from "../core/token.ts";
import { createSongApi } from "../song/api.ts";
import { h } from "../ui/dom.ts";
import { mountScreen, type Screen } from "./screen.ts";
import { chooseView, mountStart, type StartView } from "./start.ts";

const pageToken = token();
const body = document.body;
let screen: Screen | null = null;
let start: StartView | null = null;
// a later switch wins over one still loading
let seq = 0;

async function leave(): Promise<void> {
  start?.dispose();
  start = null;
  const was = screen;
  screen = null;
  await was?.dispose();
}

async function showSong(): Promise<void> {
  const mine = ++seq;
  await leave();
  body.replaceChildren();
  try {
    const mounted = await mountScreen(body, { base: "", token: pageToken, toStart: () => void showStart() });
    if (mine !== seq) await mounted.dispose();
    else screen = mounted;
  } catch (error) {
    if (mine !== seq) return;
    const back = h("button", { class: "btn btn--ghost", type: "button" }, "Мои песни");
    back.addEventListener("click", () => void showStart());
    body.replaceChildren(h("p", { class: "start__fail" }, `Песня не загрузилась: ${error instanceof Error ? error.message : String(error)} `, back));
  }
}

async function showStart(): Promise<void> {
  const mine = ++seq;
  await leave();
  if (mine !== seq) return;
  body.replaceChildren();
  start = mountStart(body, { base: "", token: pageToken, opened: () => void showSong() });
}

async function first(): Promise<void> {
  const api = createSongApi({ base: "", token: pageToken });
  let view: "start" | "song";
  try {
    const { active } = await api.songs();
    view = chooseView({ active, listed: true, asked: false });
  } catch {
    view = chooseView({ active: null, listed: false, asked: false });
  } finally {
    api.close();
  }
  if (view === "song") await showSong();
  else await showStart();
}

void first();
