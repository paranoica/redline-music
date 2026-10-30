// The keyboard map of the studio.

export type KeyAction = "play" | "loop" | "ab" | "reference" | "undo" | "redo";

export interface KeyLike {
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

// `typing`: the focus is in a field that takes text, which keeps its own keys
export function keyAction(e: KeyLike, typing: boolean): KeyAction | null {
  if (typing || e.altKey) return null;
  const mod = e.ctrlKey || e.metaKey;
  if (mod) {
    if (e.code === "KeyZ") return e.shiftKey ? "redo" : "undo";
    if (e.code === "KeyY") return "redo";
    return null;
  }
  switch (e.code) {
    case "Space":
      return "play";
    case "KeyL":
      return "loop";
    case "KeyB":
      return "ab";
    case "KeyR":
      return "reference";
    default:
      return null;
  }
}
