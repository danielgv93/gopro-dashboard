// Undo/redo over immutable snapshots. Film operations return new arrays, so a snapshot is just a reference.

export class History {
  constructor(initial, limit = 200) {
    this.limit = limit;
    this.reset(initial);
  }

  reset(initial) {
    this.past = [];
    this.present = initial;
    this.future = [];
  }

  push(next) {
    if (next === this.present) return;
    this.past = [...this.past, this.present].slice(-this.limit);
    this.present = next;
    this.future = [];
  }

  undo() {
    if (!this.past.length) return null;
    this.future = [this.present, ...this.future];
    this.present = this.past.at(-1);
    this.past = this.past.slice(0, -1);
    return this.present;
  }

  redo() {
    if (!this.future.length) return null;
    this.past = [...this.past, this.present];
    this.present = this.future[0];
    this.future = this.future.slice(1);
    return this.present;
  }

  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
}
