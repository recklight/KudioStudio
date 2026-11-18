// Which answers to a panel's commands are older than what its events have
// already said.
//
// A click that starts a job is answered with the panel's state as it was
// when the job began. A quick job -- a denoise of a short clip, a synthesis
// kudio refuses at once -- publishes its result before that answer arrives,
// and the answer, put on screen after it, would bring back the figures and
// the busy button from before. The server numbers every state it sends, events
// and answers alike; an answer numbered below the newest event of its panel
// is dropped, and the caller gets null, which every panel's show() ignores.
// describe is the exception: a panel mounts on its answer and cannot on
// null, and an event that came while it was mounting had no panel to show it,
// so describe is asked again.

export class Freshness {
  constructor() {
    this.newest = new Map();
  }

  /** Note the number of a panel event that carries the panel's state. */
  heard(event) {
    if (event?.type !== "panel" || typeof event.serial !== "number") return;
    this.newest.set(event.panel, Math.max(this.newest.get(event.panel) ?? -1, event.serial));
  }

  /** Whether *answer*, from a command of *panel*, is older than an event. */
  stale(panel, answer) {
    return Boolean(answer) && typeof answer === "object" && typeof answer.serial === "number"
      && answer.serial < (this.newest.get(panel) ?? -1);
  }

  /** The answer *run* gets to *panel*'s command *name*, or null when it is
   *  stale; a stale describe is asked again, and after three tries the last
   *  answer is kept, which is better than a panel that fails to mount. */
  async ask(panel, name, run) {
    for (let tries = 1; ; tries += 1) {
      const answer = await run();
      if (!this.stale(panel, answer)) return answer;
      if (name !== "describe") return null;
      if (tries === 3) return answer;
    }
  }
}
