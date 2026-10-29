// Undo and redo over whole settings snapshots.
const LIMIT = 200;

export class History {
  constructor(first) {
    this.past = [];
    this.future = [];
    this.now = first;
  }

  push(next) {
    if (JSON.stringify(next) === JSON.stringify(this.now)) return false;
    this.past.push(this.now);
    if (this.past.length > LIMIT) this.past.shift();
    this.now = next;
    this.future = [];
    return true;
  }

  undo() {
    if (!this.past.length) return null;
    this.future.push(this.now);
    this.now = this.past.pop();
    return this.now;
  }

  redo() {
    if (!this.future.length) return null;
    this.past.push(this.now);
    this.now = this.future.pop();
    return this.now;
  }
}
