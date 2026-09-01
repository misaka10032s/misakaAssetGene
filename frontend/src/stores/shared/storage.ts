/**
 * Small `localStorage` JSON read/write helpers shared by the store modules
 * that persist a draft across reloads (see `stores/app/drafts.ts`).
 *
 * SSR-safe: both no-op (return the fallback / do nothing) when `window` is
 * unavailable, matching the previous inline behaviour in `stores/app.ts`.
 */
export function readStoredJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") {
    return fallback;
  }
  const rawValue = window.localStorage.getItem(key);
  if (!rawValue) {
    return fallback;
  }
  try {
    return JSON.parse(rawValue) as T;
  } catch {
    return fallback;
  }
}

export function writeStoredJson(key: string, value: unknown): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(value));
}
