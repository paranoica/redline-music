// The text tab: which version the song sings, a plain hand edit saved as a new version, and the
// nudges of the lyric lines on the timeline (start bar, bars per line).

import { ApiError, type SongApi, type SongChange, type SongReply, type TextProblem, type TextReply } from "../song/api.ts";
import { placeOfProblem, textDirty } from "../song/text.ts";
import { h } from "../ui/dom.ts";
import type { LyricLayout } from "./lyrics.ts";

export interface TextHost {
  api: SongApi;
  reply(): SongReply;
  change(set: SongChange): Promise<boolean>;
  reload(): Promise<void>;
  layout(): LyricLayout;
  nudge(to: Partial<LyricLayout>): void;
  say(text: string): void;
}

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export class TextTab {
  readonly root: HTMLElement;
  readonly #host: TextHost;
  readonly #head: HTMLElement;
  readonly #area: HTMLTextAreaElement;
  readonly #foot: HTMLElement;
  #viewing: number | null = null;
  #loaded: TextReply | null = null;
  #problems: TextProblem[] = [];
  #failure: string | null = null;
  #saving = false;
  #wait = 0;

  constructor(host: TextHost) {
    this.#host = host;
    this.#head = h("div", { class: "ttab__head" });
    this.#area = h("textarea", { class: "ttab__area", spellcheck: "false", "aria-label": "Текст песни" });
    this.#foot = h("div", { class: "ttab__foot" });
    this.root = h("div", { class: "ttab" }, this.#head, this.#area, this.#foot);
    this.#area.addEventListener("input", () => this.#paintFoot());
  }

  get dirty(): boolean {
    return textDirty(true, this.#area.value, this.#loaded);
  }

  show(): void {
    const t = this.#host.reply().stations.text;
    if (t.seed === null) {
      this.#head.replaceChildren(h("p", { class: "fx__note" }, `Текста нет. Его пишет агент: ${t.command}`));
      this.#area.hidden = true;
      this.#foot.replaceChildren();
      return;
    }
    this.#area.hidden = false;
    if (this.#viewing === null || !(t.versions ?? []).includes(this.#viewing)) this.#viewing = t.version ?? t.versions?.at(-1) ?? null;
    this.#load();
    this.#paintHead();
    this.#paintFoot();
  }

  #load(): void {
    const v = this.#viewing;
    if (v === null || (this.#loaded && this.#loaded.version === v) || this.dirty) return;
    const mine = ++this.#wait;
    this.#host.api.text(v).then(
      (r) => {
        if (mine !== this.#wait) return;
        this.#loaded = r;
        this.#area.value = r.text;
        this.#failure = null;
        this.#paintFoot();
      },
      () => {
        if (mine !== this.#wait) return;
        this.#failure = "Текст не загрузился.";
        this.#paintFoot();
      },
    );
  }

  #paintHead(): void {
    const t = this.#host.reply().stations.text;
    const versions = t.versions ?? [];
    const pick = h("select", { class: "ttab__pick", "aria-label": "Версия текста" });
    for (const v of [...versions].reverse()) pick.append(h("option", { value: String(v), selected: v === this.#viewing }, `v${v}${v === t.version ? " · в песне" : ""}`));
    pick.addEventListener("change", () => {
      if (this.dirty && !confirm("Правка не сохранена. Открыть другую версию и потерять её?")) {
        pick.value = String(this.#viewing);
        return;
      }
      this.#viewing = Number(pick.value);
      this.#loaded = null;
      this.#area.value = "";
      this.show();
    });
    const sing = h("button", { class: "chip", type: "button", hidden: this.#viewing === null || this.#viewing === t.version }, "петь по этой");
    sing.addEventListener("click", async () => {
      if (t.seed === null || this.#viewing === null) return;
      if (await this.#host.change({ text: { seed: t.seed, version: this.#viewing } })) this.show();
    });
    const l = this.#host.layout();
    const minus = h("button", { class: "chip", type: "button", title: "Строки на такт раньше", disabled: l.startBar <= 0 }, "−");
    const plus = h("button", { class: "chip", type: "button", title: "Строки на такт позже" }, "+");
    minus.addEventListener("click", () => this.#host.nudge({ startBar: l.startBar - 1 }));
    plus.addEventListener("click", () => this.#host.nudge({ startBar: l.startBar + 1 }));
    const per = h("div", { class: "pickseg", role: "radiogroup", "aria-label": "Тактов на строку" });
    for (const n of [1, 2] as const) {
      const b = h("button", { type: "button", role: "radio", "aria-checked": String(l.barsPerLine === n) }, n === 1 ? "1 такт" : "2 такта");
      b.addEventListener("click", () => {
        if (l.barsPerLine !== n) this.#host.nudge({ barsPerLine: n });
      });
      per.append(b);
    }
    this.#head.replaceChildren(
      h("span", { class: "fx__lbl" }, "Версия"),
      pick,
      sing,
      h("span", { class: "spacer" }),
      h("span", { class: "fx__lbl" }, "Начало"),
      minus,
      h("span", { class: "ttab__bar num" }, `такт ${l.startBar + 1}`),
      plus,
      h("span", { class: "fx__lbl" }, "Строка"),
      per,
    );
  }

  #paintFoot(): void {
    const save = h("button", { class: "btn", type: "button", disabled: !this.dirty || this.#saving }, this.#saving ? "Сохраняю…" : "Сохранить как новую версию");
    save.addEventListener("click", () => void this.#save());
    const notes: Node[] = [];
    if (this.#failure) notes.push(h("span", { class: "ttab__err" }, this.#failure));
    for (const p of this.#problems.slice(0, 4)) notes.push(h("span", { class: "ttab__err" }, `${placeOfProblem(p)}: ${p.message}`));
    if (this.#problems.length > 4) notes.push(h("span", { class: "ttab__err" }, `и ещё ${this.#problems.length - 4}`));
    this.#foot.replaceChildren(save, ...notes);
  }

  async #save(): Promise<void> {
    const t = this.#host.reply().stations.text;
    if (t.seed === null || this.#viewing === null || this.#saving) return;
    this.#saving = true;
    this.#problems = [];
    this.#failure = null;
    this.#paintFoot();
    try {
      const made = await this.#host.api.saveText(t.seed, this.#viewing, this.#area.value);
      this.#viewing = made.version;
      this.#loaded = null;
      this.#area.value = "";
      this.#host.say(`Сохранено как v${made.version}. Чтобы петь по нему, нажми «петь по этой».`);
      await this.#host.reload().catch(() => {});
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        this.#problems = ((error.body["errors"] as TextProblem[] | undefined) ?? []).slice();
        if (!this.#problems.length) this.#failure = error.message;
      } else this.#failure = `Не сохранилось: ${message(error)}`;
    } finally {
      this.#saving = false;
      this.show();
    }
  }
}
