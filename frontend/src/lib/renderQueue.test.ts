import { describe, expect, it } from "vitest";
import { RenderQueue } from "./renderQueue";

/** A job that resolves when you tell it to. */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe("RenderQueue", () => {
  it("runs one job at a time", async () => {
    const q = new RenderQueue();
    const first = deferred();
    const running: string[] = [];

    q.push("a", () => 0, async () => {
      running.push("a");
      await first.promise;
    });
    q.push("b", () => 0, async () => {
      running.push("b");
    });

    await settle();
    // b must wait: the whole point is not handing pdfjs two at once.
    expect(running).toEqual(["a"]);
    first.resolve();
    await settle();
    expect(running).toEqual(["a", "b"]);
  });

  it("takes the nearest page first", async () => {
    const q = new RenderQueue();
    const block = deferred();
    const order: string[] = [];

    q.push("block", () => -1, async () => {
      await block.promise;
    });
    // Queued far-to-near; should run near-to-far.
    q.push("far", () => 10, async () => {
      order.push("far");
    });
    q.push("near", () => 1, async () => {
      order.push("near");
    });
    q.push("mid", () => 5, async () => {
      order.push("mid");
    });

    block.resolve();
    await settle();
    expect(order).toEqual(["near", "mid", "far"]);
  });

  it("re-reads priority as it drains, because the reader moves", async () => {
    const q = new RenderQueue();
    const block = deferred();
    const order: string[] = [];
    let viewing = 1;

    q.push("block", () => -1, async () => {
      await block.promise;
    });
    q.push("p1", () => Math.abs(1 - viewing), async () => {
      order.push("p1");
    });
    q.push("p9", () => Math.abs(9 - viewing), async () => {
      order.push("p9");
    });

    // The reader scrolled to page 9 while the queue was blocked.
    viewing = 9;
    block.resolve();
    await settle();
    expect(order).toEqual(["p9", "p1"]);
  });

  it("drops a job cancelled before its turn", async () => {
    const q = new RenderQueue();
    const block = deferred();
    const ran: string[] = [];

    q.push("block", () => -1, async () => {
      await block.promise;
    });
    const cancel = q.push("gone", () => 0, async () => {
      ran.push("gone");
    });
    q.push("stays", () => 1, async () => {
      ran.push("stays");
    });

    // Scrolled past before it ever started.
    cancel();
    block.resolve();
    await settle();
    expect(ran).toEqual(["stays"]);
  });

  it("replaces an earlier job for the same page", async () => {
    const q = new RenderQueue();
    const block = deferred();
    const ran: string[] = [];

    q.push("block", () => -1, async () => {
      await block.promise;
    });
    q.push("p3", () => 0, async () => {
      ran.push("first");
    });
    // The page remounted at a new scale before the first job ran.
    q.push("p3", () => 0, async () => {
      ran.push("second");
    });

    block.resolve();
    await settle();
    expect(ran).toEqual(["second"]);
  });

  it("carries on after a job throws", async () => {
    const q = new RenderQueue();
    const ran: string[] = [];

    q.push("bad", () => 0, async () => {
      throw new Error("render failed");
    });
    q.push("good", () => 1, async () => {
      ran.push("good");
    });

    await settle();
    expect(ran).toEqual(["good"]);
  });
});
