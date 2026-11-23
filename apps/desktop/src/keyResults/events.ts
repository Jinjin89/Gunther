export const KEY_RESULTS_CHANGED_EVENT = "gunther:key-results-updated";

export interface KeyResultsChange {
  baseId: string;
  countsChanged: boolean;
}

export function keyResultsChanged(baseId: string, countsChanged = false) {
  window.dispatchEvent(new CustomEvent<KeyResultsChange>(KEY_RESULTS_CHANGED_EVENT, {
    detail: { baseId, countsChanged },
  }));
}
