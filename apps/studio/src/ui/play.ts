// The round play/pause button of a clip that is listened to one at a time.

import { h, icon } from "./dom.ts";

const PLAY = "M7 4.5v15l12.5-7.5z";
const PAUSE = "M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z";

export function playButton(label: string, on: boolean, click: () => void, over = "поверх бита"): HTMLButtonElement {
  const b = h("button", { class: "tplay", type: "button", "aria-pressed": String(on), "aria-label": `Слушать ${label}${over ? ` ${over}` : ""}` });
  b.append(icon(PLAY, "play__icon play__icon--play"), icon(PAUSE, "play__icon play__icon--pause"));
  b.addEventListener("click", click);
  return b;
}
