// The song page: the open song's screen, or the list of songs when none is open.

import { token } from "../core/token.ts";
import { ApiError, createSongApi } from "../song/api.ts";
import { h } from "../ui/dom.ts";
import { mountScreen, type Screen } from "./screen.ts";

const pageToken = token();
const body = document.body;
let screen: Screen | null = null;

async function showSong(): Promise<void> {
  body.replaceChildren();
  try {
    screen = await mountScreen(body, { base: "", token: pageToken, toStart: () => void showStart() });
  } catch (error) {
    body.replaceChildren(h("p", { class: "start__fail" }, `Песня не загрузилась: ${error instanceof Error ? error.message : String(error)}`));
  }
}

// a stand-in for the start screen: the songs by name, a click opens one
async function showStart(): Promise<void> {
  await screen?.dispose();
  screen = null;
  const api = createSongApi({ base: "", token: pageToken });
  const list = h("div", { class: "songs" });
  const note = h("p", { class: "start__note", role: "status" });
  body.replaceChildren(h("section", { class: "start" }, h("div", { class: "start__top" }, h("span", { class: "brand__mark", "aria-hidden": "true" }), h("h1", {}, "Мои песни")), list, note));
  try {
    const { songs } = await api.songs();
    if (!songs.length) note.textContent = "Песен пока нет: создай первую командой redline song new.";
    for (const s of songs) {
      const card = h("button", { class: "song", type: "button" }, h("h3", {}, s.name), h("p", {}, `${s.beat_label} · дублей: ${s.takes}`));
      card.addEventListener("click", async () => {
        try {
          await api.openSong(s.name);
          api.close();
          await showSong();
        } catch (error) {
          note.textContent = error instanceof ApiError && error.code === "take_running" ? "Идёт запись дубля: дождись её конца." : `Не открылась: ${error instanceof Error ? error.message : String(error)}`;
        }
      });
      list.append(card);
    }
  } catch (error) {
    note.textContent = `Список песен не загрузился: ${error instanceof Error ? error.message : String(error)}`;
  }
}

async function start(): Promise<void> {
  const api = createSongApi({ base: "", token: pageToken });
  try {
    const { active } = await api.songs();
    api.close();
    if (active) await showSong();
    else await showStart();
  } catch {
    // a server without the songs list serves one song
    api.close();
    await showSong();
  }
}

void start();
