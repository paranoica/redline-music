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
  // edits that would be lost by disposing the app
  dirty?(): boolean;
  // settings from outside the app (a mix variant), applied as a preset is
  load?(settings: Record<string, unknown>, label: string): void;
  // out of sight: the sound stops where it is and goes on from there when let go
  hold?(on: boolean): void;
}

export type Mount = (root: HTMLElement, options: MountOptions) => Promise<Mounted>;

export function apiRoot(base: string | undefined, fallback: string): string {
  const trimmed = (base ?? "").replace(/\/+$/, "");
  return trimmed === "" ? fallback : trimmed;
}

export function apiPath(root: string, path: string): string {
  return `${root}${path.startsWith("/") ? path : `/${path}`}`;
}

interface Suspendable {
  readonly state: string;
  suspend(): Promise<void>;
  resume(): Promise<void>;
}

// an app's hold(): out of sight its audio stops where it is; let go, it goes on only if it ran
export function audioHold(context: () => Suspendable | null | undefined): (on: boolean) => void {
  let holding = false;
  let suspended = false;
  return (on) => {
    const ctx = context();
    if (!ctx || on === holding) return;
    holding = on;
    if (on) {
      suspended = ctx.state === "running";
      if (suspended) void ctx.suspend().catch(() => {});
    } else if (suspended) {
      suspended = false;
      void ctx.resume().catch(() => {});
    }
  };
}
