// The page token comes once in the address and is dropped from it, so it stays out of the
// history and screenshots. The tab keeps it, so a reload after a conflict still gets in.

const KEY = "redline-token";

interface TabStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function pageToken(search: string, tab: TabStore | null, clearAddress: () => void): string {
  const given = new URLSearchParams(search).get("t");
  if (given) {
    clearAddress();
    try {
      tab?.setItem(KEY, given);
    } catch {
      // a blocked storage only costs the reload
    }
    return given;
  }
  try {
    return tab?.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

function tabStore(): TabStore | null {
  try {
    return sessionStorage;
  } catch {
    return null;
  }
}

export function token(): string {
  return pageToken(location.search, tabStore(), () => history.replaceState(null, "", location.pathname));
}
