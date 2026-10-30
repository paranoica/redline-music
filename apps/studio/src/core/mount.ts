// The contract of a mountable app, and the paths its api builds from the base it is given.

export interface MountOptions {
  // the prefix of the app's routes; empty or missing means the app's own default
  base?: string;
  token: string;
  // whether the keyboard is the app's from the start (default yes)
  active?: boolean;
}

export interface Mounted {
  dispose(): Promise<void> | void;
  setActive?(on: boolean): void;
}

export type Mount = (root: HTMLElement, options: MountOptions) => Promise<Mounted>;

export function apiRoot(base: string | undefined, fallback: string): string {
  const trimmed = (base ?? "").replace(/\/+$/, "");
  return trimmed === "" ? fallback : trimmed;
}

export function apiPath(root: string, path: string): string {
  return `${root}${path.startsWith("/") ? path : `/${path}`}`;
}
