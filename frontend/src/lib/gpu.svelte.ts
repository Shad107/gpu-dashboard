// GPU picker store — which GPU index the user is currently inspecting.
// Default 0 (single-GPU rigs unaffected). Persists to localStorage. Optional
// `?gpu=N` URL override for screenshot tooling + sharable links.

const STORAGE_KEY = "gpu-dashboard-selected-gpu";

function loadInitial(): number {
  if (typeof window === "undefined") return 0;
  const m = location.search.match(/[?&]gpu=(\d+)/);
  if (m) return parseInt(m[1], 10);
  if (typeof localStorage === "undefined") return 0;
  const v = localStorage.getItem(STORAGE_KEY);
  return v !== null ? parseInt(v, 10) || 0 : 0;
}

class GpuStore {
  selected = $state<number>(loadInitial());

  set(idx: number): void {
    if (this.selected === idx) return;
    this.selected = idx;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, String(idx));
    }
  }
}

export const gpu = new GpuStore();
