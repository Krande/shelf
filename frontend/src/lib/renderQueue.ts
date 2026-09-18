/**
 * One page render at a time, nearest the viewport first.
 *
 * pdf.js has a single worker thread and the main thread has one canvas
 * compositor, so firing every mounted page's render at once does not
 * make any of them finish sooner — it makes all of them finish later,
 * and the one the reader is actually looking at finishes last as often
 * as first. pdf.js's own viewer serialises for the same reason.
 *
 * Priority is distance from the page in view, so the overscan either
 * side of the viewport is rendered after what is in it, and a reader
 * who keeps scrolling never waits on work for pages they have left.
 */

interface Job {
  key: string;
  priority: () => number;
  run: () => Promise<void>;
  cancelled: boolean;
}

export class RenderQueue {
  private queue: Job[] = [];
  private running = false;

  /**
   * Queue a render. The returned function removes it — call it from an
   * effect cleanup, so a page scrolled past before its turn never runs
   * at all rather than running into a cleanup that has to undo it.
   */
  push(key: string, priority: () => number, run: () => Promise<void>): () => void {
    // A page that remounts before its earlier job ran replaces it; two
    // renders of the same page would only race each other.
    this.remove(key);
    const job: Job = { key, priority, run, cancelled: false };
    this.queue.push(job);
    void this.drain();
    return () => {
      job.cancelled = true;
      this.remove(key);
    };
  }

  private remove(key: string): void {
    const at = this.queue.findIndex((j) => j.key === key);
    if (at >= 0) this.queue.splice(at, 1);
  }

  private async drain(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (this.queue.length > 0) {
        // Re-sorted every time rather than once on insert: the reader
        // moves while the queue drains, so what is nearest the viewport
        // is not what was nearest when these were queued.
        this.queue.sort((a, b) => a.priority() - b.priority());
        const job = this.queue.shift()!;
        if (job.cancelled) continue;
        try {
          await job.run();
        } catch {
          // One page failing is that page's problem; the queue carries
          // on or the reader shows nothing at all.
        }
      }
    } finally {
      this.running = false;
    }
  }
}
