// The rack card: the effects the vocal goes through in order, each with its params.
// Automation lanes from a saved file ride along untouched; a param with a lane is shown
// greyed out, since the lane sets it.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, label, onClick) {
  const b = el("button", "btn btn--ghost", text);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

function shown(value) {
  return String(Number(Number(value).toPrecision(4)));
}

export class RackCard {
  constructor(root, effects, onChange) {
    this.effects = Object.fromEntries(effects.map((e) => [e.effect, e]));
    this.onChange = onChange;
    this.rack = [];
    const card = el("article", "card");
    card.dataset.group = "rack";
    card.dataset.empty = "false";
    const head = el("header", "card__head");
    head.append(el("h2", "card__title", "Рэк эффектов"));
    this.rows = el("div", "card__rows");
    card.append(head, this.rows);
    this.adder = el("div", "row row--choice");
    if (effects.length) {
      const pick = el("span", "dropdown");
      this.select = el("select", "dropdown__select");
      this.select.setAttribute("aria-label", "Какой эффект добавить");
      for (const e of effects) this.select.append(new Option(e.effect, e.effect));
      pick.append(this.select);
      const add = button("Добавить", "Добавить эффект в конец рэка", () =>
        this.#commit([...this.#copy(), { effect: this.select.value, on: true, params: {}, automation: [] }]),
      );
      this.adder.append(el("span", "row__label", "Новый эффект"), pick, add);
    } else {
      this.adder.append(el("span", "row__label", "Рэк недоступен: модуль redline_dsp не собран."));
    }
    root.append(card);
  }

  #copy() {
    return structuredClone(this.rack);
  }

  #commit(rack) {
    this.onChange(rack);
  }

  #change(index, edit) {
    const rack = this.#copy();
    edit(rack[index], rack);
    this.#commit(rack);
  }

  #move(index, by) {
    const rack = this.#copy();
    const [slot] = rack.splice(index, 1);
    rack.splice(index + by, 0, slot);
    this.#commit(rack);
  }

  #head(slot, index) {
    const spec = this.effects[slot.effect];
    const row = el("div", "row row--switch");
    const name = `${index + 1}. ${slot.effect}${spec?.offline_only ? " (только офлайн)" : ""}`;
    row.append(el("span", "row__label", name));
    const tools = el("span", "rack__tools");
    const up = button("↑", `Выше: ${slot.effect}`, () => this.#move(index, -1));
    const down = button("↓", `Ниже: ${slot.effect}`, () => this.#move(index, 1));
    up.disabled = index === 0;
    down.disabled = index === this.rack.length - 1;
    const sw = el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-label", `Включён: ${slot.effect}`);
    sw.setAttribute("aria-checked", String(slot.on !== false));
    sw.addEventListener("click", () => this.#change(index, (s) => (s.on = s.on === false)));
    tools.append(up, down, sw);
    const remove = el("button", "icon-btn", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Убрать: ${slot.effect}`);
    remove.addEventListener("click", () => this.#change(index, (_, rack) => rack.splice(index, 1)));
    row.append(tools, remove);
    return row;
  }

  #param(slot, index, param) {
    const value = slot.params?.[param.name] ?? param.default;
    const automated = (slot.automation || []).some((lane) => lane.param === param.name);
    const set = (v) => this.#change(index, (s) => (s.params = { ...s.params, [param.name]: v }));
    if (param.choices) {
      const row = el("div", "row row--choice");
      row.append(el("span", "row__label", param.name));
      const pick = el("span", "dropdown");
      const select = el("select", "dropdown__select");
      select.setAttribute("aria-label", `${slot.effect}: ${param.name}`);
      for (const choice of param.choices) select.append(new Option(choice, choice));
      select.value = typeof value === "number" ? param.choices[value] : value;
      select.addEventListener("change", () => set(select.value));
      pick.append(select);
      row.append(pick, el("span"));
      return row;
    }
    const row = el("div", "row");
    row.dataset.disabled = String(automated);
    const label = el("span", "row__label", automated ? `${param.name} (автоматизация)` : param.name);
    const slider = el("div", "slider");
    const input = el("input", "slider__input");
    Object.assign(input, { type: "range", min: param.low, max: param.high, step: "any", value });
    input.setAttribute("aria-label", `${slot.effect}: ${param.name}`);
    input.disabled = automated;
    slider.append(input);
    const box = el("label", "number");
    const number = el("input", "number__input");
    Object.assign(number, { type: "number", min: param.low, max: param.high, step: "any" });
    number.value = shown(value);
    number.disabled = automated;
    number.setAttribute("aria-label", `${slot.effect}: ${param.name}, точное значение`);
    box.append(number, el("span", "number__unit", param.unit));
    const fill = () => {
      const share = ((Number(input.value) - param.low) / (param.high - param.low)) * 100;
      input.style.setProperty("--fill", `${share}%`);
    };
    fill();
    input.addEventListener("input", () => {
      number.value = shown(input.value);
      fill();
    });
    input.addEventListener("change", () => set(Number(input.value)));
    number.addEventListener("change", () => {
      const v = Number(number.value);
      if (Number.isFinite(v)) set(Math.min(param.high, Math.max(param.low, v)));
      else number.value = shown(value);
    });
    row.append(label, slider, box, el("span"));
    return row;
  }

  show(rack) {
    this.rack = structuredClone(rack || []);
    this.rows.replaceChildren();
    this.rack.forEach((slot, index) => {
      this.rows.append(this.#head(slot, index));
      for (const param of this.effects[slot.effect]?.params || []) {
        this.rows.append(this.#param(slot, index, param));
      }
    });
    this.rows.append(this.adder);
  }
}
