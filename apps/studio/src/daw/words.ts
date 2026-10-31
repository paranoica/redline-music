// Words the screen shows: take names and Russian plurals.

export function takeLabel(name: string): string {
  const m = /^take-(\d+)\.wav$/.exec(name);
  return m ? `дубль ${m[1]}` : name;
}

// one, few, many: 1 дубль, 2 дубля, 5 дублей, 11 дублей, 21 дубль
export function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  const last = n % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}
