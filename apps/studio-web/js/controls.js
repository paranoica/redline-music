// One card per group of settings, built from the server's schema.
// A setting reports a change on release, never while a slider is dragged.

// a setting greys out while its switch is off (or, for master_lufs, while auto is on)
const DEPENDS = {
  comp_threshold_db: ["chain_on", true],
  comp_ratio: ["chain_on", true],
  comp_attack_ms: ["chain_on", true],
  comp_release_ms: ["chain_on", true],
  drive_db: ["chain_on", true],
  clip_on: ["chain_on", true],
  clip_below_peak_db: ["clip_on", true],
  octave_down_db: ["octave_down_on", true],
  octave_up_db: ["octave_up_on", true],
  doubles_db: ["doubles_on", true],
  doubles_left_ms: ["doubles_on", true],
  doubles_right_ms: ["doubles_on", true],
  doubles_wander_ms: ["doubles_on", true],
  bitcrush_bits: ["bitcrush_on", true],
  bitcrush_hold: ["bitcrush_on", true],
  master_auto: ["master_on", true],
  master_lufs: ["master_auto", false],
};

// what "bypass" sets for each group: the group does nothing
export const NEUTRAL = {
  chain: { chain_on: false },
  eq: { eq_fit: false, eq_200: 0, eq_400: 0, eq_1000: 0, eq_2500: 0, eq_5000: 0, eq_8000: 0 },
  voice: { hard_tune: 0, formant: 1, voice_octave: 0, nightcore: false },
  layers: { octave_down_on: false, octave_up_on: false, doubles_on: false, bitcrush_on: false },
  beat: { beat_db: 0, bass_drive_db: 0 },
  master: { master_on: false },
};

// the first render stage each group touches; every stage after it re-runs too
export const STAGE_OF = { chain: 0, eq: 0, voice: 1, layers: 2, beat: 3, master: 4 };

const ICONS = {
  reset:
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12a7 7 0 1 0 2.1-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M4 4v4.5h4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function decimals(step) {
  const text = String(step);
  return text.includes(".") ? text.split(".")[1].length : 0;
}

function format(spec, value) {
  return Number(value).toFixed(decimals(spec.step));
}

export class Controls {
  constructor(root, schema, tip, onChange) {
    this.root = root;
    this.tip = tip;
    this.onChange = onChange;
    this.specs = Object.fromEntries(schema.settings.map((s) => [s.key, s]));
    this.groups = schema.groups;
    this.rows = {};
    this.cards = {};
    this.values = {};
    this.#build(schema);
  }

  #build(schema) {
    for (const [group, title] of Object.entries(schema.groups)) {
      const card = el("article", "card");
      card.dataset.group = group;
      const head = el("header", "card__head");
      head.append(el("h2", "card__title", title));
      const changed = el("span", "card__changed num");
      head.append(changed);
      const bypass = el("label", "card__bypass");
      const bypassSwitch = el("button", "switch");
      bypassSwitch.type = "button";
      bypassSwitch.setAttribute("role", "switch");
      bypassSwitch.setAttribute("aria-checked", "false");
      bypassSwitch.setAttribute("aria-label", `Обойти: ${title}`);
      bypassSwitch.addEventListener("click", () => this.#bypass(group));
      bypass.append("обойти", bypassSwitch);
      head.append(bypass);
      const rows = el("div", "card__rows");
      card.append(head, rows);
      this.cards[group] = { card, changed, bypassSwitch, rows, before: null };
      this.root.append(card);
    }
    for (const spec of schema.settings) {
      this.cards[spec.group].rows.append(this.#row(spec));
    }
    for (const [group, card] of Object.entries(this.cards)) {
      const basic = schema.settings.some((s) => s.group === group && s.basic);
      card.card.dataset.empty = String(!basic);
    }
  }

  #label(spec) {
    const label = el("span", "row__label");
    label.append(el("span", "row__label-text", spec.label));
    const help = el("button", "icon-btn help", "?");
    help.type = "button";
    help.setAttribute("aria-label", `Что это: ${spec.label}`);
    const show = () => this.#showTip(help, spec.help);
    help.addEventListener("mouseenter", show);
    help.addEventListener("focus", show);
    help.addEventListener("mouseleave", () => (this.tip.hidden = true));
    help.addEventListener("blur", () => (this.tip.hidden = true));
    label.append(help);
    return label;
  }

  #resetButton(spec) {
    const reset = el("button", "icon-btn reset");
    reset.type = "button";
    reset.innerHTML = ICONS.reset;
    reset.setAttribute("aria-label", `Сбросить: ${spec.label}`);
    reset.title = "Вернуть по умолчанию";
    reset.addEventListener("click", () => this.#commit({ [spec.key]: spec.default }));
    return reset;
  }

  #row(spec) {
    const isBool = spec.kind === "bool";
    const isChoice = !isBool && spec.choices.length > 0;
    const row = el("div", `row${isBool ? " row--switch" : isChoice ? " row--choice" : ""}`);
    row.append(this.#label(spec));
    const handle = { row, spec };
    if (isBool) {
      const sw = el("button", "switch");
      sw.type = "button";
      sw.setAttribute("role", "switch");
      sw.setAttribute("aria-label", spec.label);
      sw.addEventListener("click", () => this.#commit({ [spec.key]: !this.values[spec.key] }));
      handle.switch = sw;
      row.append(sw);
    } else if (isChoice) {
      const group = el("div", "segments");
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-label", spec.label);
      handle.segments = spec.choices.map((choice) => {
        const item = el("button", "segments__item", `${choice > 0 ? "+" : ""}${choice}`);
        item.type = "button";
        item.setAttribute("role", "radio");
        item.addEventListener("click", () => this.#commit({ [spec.key]: choice }));
        group.append(item);
        return [choice, item];
      });
      row.append(group);
    } else {
      const slider = el("div", "slider");
      const tick = el("span", "slider__tick");
      tick.style.left = `calc(${((spec.default - spec.low) / (spec.high - spec.low)) * 100}% - 0.5px)`;
      const input = el("input", "slider__input");
      Object.assign(input, { type: "range", min: spec.low, max: spec.high, step: spec.step });
      input.setAttribute("aria-label", spec.label);
      slider.append(tick, input);
      const box = el("label", "number");
      const number = el("input", "number__input");
      Object.assign(number, { type: "number", min: spec.low, max: spec.high, step: spec.step });
      number.setAttribute("aria-label", `${spec.label}, точное значение`);
      box.append(number, el("span", "number__unit", spec.unit));
      input.addEventListener("input", () => {
        number.value = format(spec, input.value);
        this.#fill(input, spec);
      });
      input.addEventListener("change", () => this.#commit({ [spec.key]: Number(input.value) }));
      number.addEventListener("change", () => {
        const value = Math.min(spec.high, Math.max(spec.low, Number(number.value)));
        if (Number.isFinite(value)) {
          this.#commit({ [spec.key]: spec.kind === "int" ? Math.round(value) : value });
        } else {
          number.value = format(spec, this.values[spec.key]);
        }
      });
      handle.input = input;
      handle.number = number;
      row.append(slider, box);
    }
    row.append(this.#resetButton(spec));
    row.dataset.basic = String(Boolean(spec.basic));
    this.rows[spec.key] = handle;
    return row;
  }

  #fill(input, spec) {
    const share = ((Number(input.value) - spec.low) / (spec.high - spec.low)) * 100;
    input.style.setProperty("--fill", `${share}%`);
  }

  #showTip(anchor, text) {
    this.tip.textContent = text;
    this.tip.hidden = false;
    const box = anchor.getBoundingClientRect();
    const tip = this.tip.getBoundingClientRect();
    const left = Math.min(window.innerWidth - tip.width - 8, Math.max(8, box.left - 12));
    const top = box.bottom + 8 + tip.height > window.innerHeight ? box.top - tip.height - 8 : box.bottom + 8;
    this.tip.style.left = `${left}px`;
    this.tip.style.top = `${top}px`;
  }

  #commit(change) {
    this.onChange({ ...this.values, ...change }, Object.keys(change));
  }

  #bypass(group) {
    const card = this.cards[group];
    const neutral = NEUTRAL[group];
    if (card.before) {
      this.#commit(card.before);
      card.before = null;
    } else {
      card.before = Object.fromEntries(Object.keys(neutral).map((k) => [k, this.values[k]]));
      this.#commit(neutral);
    }
  }

  show(values) {
    this.values = { ...values };
    const changedByGroup = Object.fromEntries(Object.keys(this.groups).map((g) => [g, 0]));
    for (const [key, handle] of Object.entries(this.rows)) {
      const { spec, row } = handle;
      const value = values[key];
      const changed = value !== spec.default;
      if (changed) changedByGroup[spec.group]++;
      row.dataset.changed = String(changed);
      const dep = DEPENDS[key];
      row.dataset.disabled = String(Boolean(dep) && values[dep[0]] !== dep[1]);
      if (handle.switch) {
        handle.switch.setAttribute("aria-checked", String(Boolean(value)));
      } else if (handle.segments) {
        for (const [choice, item] of handle.segments) {
          item.setAttribute("aria-checked", String(choice === value));
        }
      } else {
        handle.input.value = value;
        handle.number.value = format(spec, value);
        this.#fill(handle.input, spec);
      }
    }
    for (const [group, card] of Object.entries(this.cards)) {
      const count = changedByGroup[group];
      card.changed.textContent = count ? `изменено: ${count}` : "";
      // bypassed only by the switch: a group at its neutral defaults is simply untouched
      const neutral = NEUTRAL[group];
      if (card.before && !Object.entries(neutral).every(([k, v]) => values[k] === v)) {
        card.before = null;
      }
      const bypassed = Boolean(card.before);
      card.card.dataset.bypassed = String(bypassed);
      card.bypassSwitch.setAttribute("aria-checked", String(bypassed));
    }
  }
}
