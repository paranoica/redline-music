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

// puts the markup into root; returns the nodes made, to look inside and to take out again
export function insertMarkup(root: HTMLElement, html: string): ChildNode[] {
  const template = root.ownerDocument.createElement("template");
  template.innerHTML = html;
  const nodes = [...template.content.childNodes];
  root.append(template.content);
  return nodes;
}

// finds [data-ref=name] among the inserted nodes only
export function refsIn(nodes: readonly Node[]): <T extends HTMLElement>(name: string) => T {
  return <T extends HTMLElement>(name: string): T => {
    const selector = `[data-ref="${name}"]`;
    for (const node of nodes) {
      if (node.nodeType !== 1) continue;
      const element = node as Element;
      const hit = element.matches(selector) ? element : element.querySelector(selector);
      if (hit) return hit as T;
    }
    throw new Error(`the markup has no [data-ref=${name}]`);
  };
}

const openPanels = new Set<{ opener: HTMLElement; finish: () => void }>();

// closes the panels opened from inside root, so their document listeners go
export function closeDismissables(root: HTMLElement): void {
  for (const panel of [...openPanels]) if (root.contains(panel.opener)) panel.finish();
}

// closes on Escape and on a press outside; returns the closer
export function dismissable(panel: HTMLElement, opener: HTMLElement, close: () => void): () => void {
  const doc = opener.ownerDocument;
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
  const entry = { opener, finish: () => finish() };
  const finish = () => {
    openPanels.delete(entry);
    doc.removeEventListener("keydown", onKey, true);
    doc.removeEventListener("pointerdown", onPress, true);
    close();
  };
  openPanels.add(entry);
  doc.addEventListener("keydown", onKey, true);
  doc.addEventListener("pointerdown", onPress, true);
  return finish;
}

let uid = 0;
export function nextId(prefix: string): string {
  uid += 1;
  return `${prefix}-${uid}`;
}
