/**
 * Audio goes to the live socket in the order it was captured. Pieces captured while the
 * socket was opening wait in a queue; a new piece may go straight out only when the socket is
 * open and nothing is waiting, otherwise it joins the queue behind the older ones.
 */
export function offerAudio<T>(queue: T[], item: T, socketOpen: boolean, send: (item: T) => void): boolean {
  if (socketOpen && queue.length === 0) {
    send(item);
    return true;
  }
  queue.push(item);
  return false;
}

/** Send everything waiting, oldest first, and empty the queue. Returns how many went. */
export function flushAudio<T>(queue: T[], send: (item: T) => void): number {
  const waiting = queue.splice(0);
  waiting.forEach(send);
  return waiting.length;
}
