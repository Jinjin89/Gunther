/** Tell the top bar that something just started or stopped, so it looks again at once. */
export const ACTIVITY_CHANGED_EVENT = "gunther:activity-changed";
export const kickActivity = () => { if (typeof window !== "undefined") window.dispatchEvent(new Event(ACTIVITY_CHANGED_EVENT)); };
