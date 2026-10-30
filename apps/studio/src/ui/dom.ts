// A small element helper: the page builds its DOM by hand, without a framework.

type Attrs = Record<string, string | number | boolean | undefined>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: (Node | string | null | undefined | false)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue;
    if (key === "class") node.className = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

const SVG = "http://www.w3.org/2000/svg";

// an icon from path data, hidden from screen readers
export function icon(d: string, className = "icon"): SVGSVGElement {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", className);
  for (const part of d.split("|")) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", part);
    svg.append(path);
  }
  return svg;
}

export function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`the page has no #${id}`);
  return node as T;
}

// closes on Escape and on a press outside; returns the closer
export function dismissable(panel: HTMLElement, opener: HTMLElement, close: () => void): () => void {
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      finish();
      opener.focus();
    }
  };
  const onPress = (e: PointerEvent) => {
    const target = e.target as Node;
    if (!panel.contains(target) && !opener.contains(target)) finish();
  };
  const finish = () => {
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("pointerdown", onPress, true);
    close();
  };
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("pointerdown", onPress, true);
  return finish;
}

let uid = 0;
export function nextId(prefix: string): string {
  uid += 1;
  return `${prefix}-${uid}`;
}
