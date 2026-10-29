// The marks page: pick a text, mark its lines. Every click is saved at once; a line
// repeated in a hook carries one mark, so all its copies light up together.
const token = new URLSearchParams(location.search).get("t") || "";
const asked = new URLSearchParams(location.hash.slice(1)).get("seed");
history.replaceState(null, "", location.pathname);

const LABELS = { cringe: "кринж", good: "норм" };
const KEYS = { c: "cringe", с: "cringe", n: "good", т: "good", 0: null };

const $ = (id) => document.getElementById(id);
const state = { texts: [], current: null, marks: {}, rows: [] };

async function call(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: {
      "X-Token": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status}`);
  return data;
}

function status(text, error = false) {
  const el = $("status");
  el.textContent = text;
  el.dataset.state = error ? "error" : "ok";
}

function key(text) {
  return `${text.seed}:${text.version}`;
}

function label(text) {
  return `seed ${text.seed} · v${text.version}`;
}

function renderList() {
  const list = $("texts");
  const pick = $("pick");
  list.replaceChildren();
  pick.replaceChildren();
  for (const text of state.texts) {
    const current = state.current && key(text) === key(state.current);
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "texts__item";
    button.setAttribute("aria-current", current ? "true" : "false");
    const name = document.createElement("span");
    name.textContent = label(text);
    const count = document.createElement("span");
    count.className = "texts__count";
    count.textContent = text.marked ? String(text.marked) : "";
    button.append(name, count);
    button.addEventListener("click", () => open(text));
    item.append(button);
    list.append(item);
    const option = new Option(label(text), key(text), false, current);
    pick.append(option);
  }
}

function tally() {
  const values = Object.values(state.marks);
  const bad = values.filter((m) => m === "cringe").length;
  const good = values.filter((m) => m === "good").length;
  $("tally").textContent = `кринж ${bad} · норм ${good}`;
}

function paint() {
  for (const row of state.rows) {
    const mark = state.marks[row.dataset.text];
    if (mark) row.dataset.mark = mark;
    else delete row.dataset.mark;
    for (const button of row.querySelectorAll(".mark")) {
      button.setAttribute("aria-pressed", String(button.dataset.mark === mark));
    }
  }
  tally();
}

async function mark(line, value) {
  const text = state.current;
  const before = state.marks[line] || null;
  const next = before === value ? null : value;
  try {
    const answer = await call("POST", "/api/mark", {
      seed: text.seed,
      version: text.version,
      line,
      mark: next,
    });
    // quick clicks are written and answered in any order; each answer holds the whole
    // text as of its write, and rev says which write came last
    if (answer.rev <= (text.rev || 0)) return;
    text.rev = answer.rev;
    text.marked = Object.keys(answer.marks).length;
    if (text === state.current) {
      state.marks = answer.marks;
      paint();
    }
    renderList();
    status(next ? `${LABELS[next]}: сохранено` : "отметка снята");
  } catch (error) {
    status(`не сохранилось: ${error.message}`, true);
  }
}

function row(line) {
  const item = document.createElement("li");
  if (line.kind !== "line") {
    item.className = `line line--${line.kind}`;
    item.textContent = line.kind === "section" ? line.text.trim().slice(1, -1) : "";
    return item;
  }
  item.className = "line";
  item.dataset.text = line.text;
  item.tabIndex = -1;
  const words = document.createElement("span");
  words.className = "line__text";
  words.textContent = line.text;
  const marks = document.createElement("span");
  marks.className = "line__marks";
  for (const value of ["cringe", "good"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `mark mark--${value}`;
    button.dataset.mark = value;
    button.textContent = LABELS[value];
    button.addEventListener("click", () => mark(line.text, value));
    marks.append(button);
  }
  item.append(words, marks);
  item.addEventListener("focus", () => (state.focused = item));
  item.addEventListener("click", () => item.focus());
  return item;
}

async function open(text) {
  try {
    const data = await call("GET", `/api/text?seed=${text.seed}&version=${text.version}`);
    state.current = text;
    state.marks = data.marks;
    const lines = data.lines.map(row);
    state.rows = lines.filter((li) => li.dataset.text !== undefined);
    $("lines").replaceChildren(...lines);
    $("title").textContent = label(text);
    paint();
    renderList();
    status("готово");
    state.rows[0]?.focus({ preventScroll: true });
  } catch (error) {
    status(`не открылся: ${error.message}`, true);
  }
}

function move(step) {
  const at = state.rows.indexOf(document.activeElement.closest?.(".line"));
  const next = state.rows[Math.max(0, Math.min(state.rows.length - 1, at + step))];
  next?.focus();
  next?.scrollIntoView({ block: "nearest" });
}

document.addEventListener("keydown", (event) => {
  if (event.target.closest("select") || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "ArrowDown" || event.key === "j") {
    event.preventDefault();
    move(1);
    return;
  }
  if (event.key === "ArrowUp" || event.key === "k") {
    event.preventDefault();
    move(-1);
    return;
  }
  const value = KEYS[event.key.toLowerCase()];
  const line = document.activeElement.closest?.(".line")?.dataset.text;
  if (value === undefined || line === undefined) return;
  event.preventDefault();
  if (value === null) {
    if (state.marks[line]) mark(line, state.marks[line]);
  } else {
    mark(line, value);
  }
});

$("pick").addEventListener("change", (event) => {
  const text = state.texts.find((t) => key(t) === event.target.value);
  if (text) open(text);
});

async function start() {
  try {
    state.texts = await call("GET", "/api/texts");
  } catch (error) {
    status(`нет связи: ${error.message}`, true);
    return;
  }
  if (!state.texts.length) {
    $("empty").hidden = false;
    status("пусто");
    return;
  }
  const first = state.texts.find((t) => String(t.seed) === asked) || state.texts[0];
  open(first);
}

start();
