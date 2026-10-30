"""Undo and redo: every edit is an op with its inverse, grouped into steps.

A step is what one undo takes back: one click on the grid, one drag in the
piano roll, one whole recorded take, one instrument change, or one turn of a
knob (changes to the same value in quick succession merge into one step).
"""

import time

from songlink import model

MERGE_S = 1.0     # changes to one value this close together are one step


def _value_at(project, path):
    node, key = model.resolve(project, path)
    return node[key]


def inverse(project, op):
    """The op that undoes ``op`` on ``project`` as it is now (before ``op``)."""
    kind = op["op"]
    if kind == "set":
        node, key = model.resolve(project, op["path"])
        old = node[key] if (isinstance(node, list) or key in node) else None
        return {"op": "set", "path": op["path"], "value": model._copy(old)}
    if kind == "insert":
        node, key = model.resolve(project, op["path"])
        index = len(node) if key == -1 or key >= len(node) else key
        return {"op": "remove", "path": op["path"][:-1] + [index]}
    if kind == "remove":
        node, key = model.resolve(project, op["path"])
        return {"op": "insert", "path": op["path"], "value": model._copy(node[key])}
    raise ValueError("can't undo %r" % kind)


class History:
    def __init__(self, apply):
        self.apply = apply            # sends an op to the project and the engines
        self.undo_steps = []
        self.redo_steps = []
        self.open = None              # the step being built, if grouping
        self.last_at = 0.0
        self.version = 0              # counts changes, so "unsaved" is a comparison

    def begin(self):
        if self.open is None:
            self.open = []

    def end(self):
        step, self.open = self.open, None
        if step:
            self.undo_steps.append(step)
            self.redo_steps.clear()
        self.last_at = 0.0        # the next change starts a step of its own

    def do(self, project, op, merge=False):
        """Apply ``op`` as an undoable edit. With ``merge``, a "set" of the same
        value as the last step, within MERGE_S, joins that step: a knob drag
        or a held button is one undo, back to where it started."""
        now = time.monotonic()
        self.version += 1
        if (merge and self.open is None and self.undo_steps and len(self.undo_steps[-1]) == 1
                and op["op"] == "set" and now - self.last_at < MERGE_S):
            last_op, last_inv = self.undo_steps[-1][0]
            if last_op["op"] == "set" and last_op["path"] == op["path"]:
                self.apply(op)
                self.undo_steps[-1] = [(op, last_inv)]
                self.redo_steps.clear()
                self.last_at = now
                return
        inv = inverse(project, op)
        self.apply(op)
        self.last_at = now
        if self.open is not None:
            self.open.append((op, inv))
        else:
            self.undo_steps.append([(op, inv)])
            self.redo_steps.clear()

    def undo(self):
        if not self.undo_steps:
            return False
        step = self.undo_steps.pop()
        self.version += 1
        for op, inv in reversed(step):
            self.apply(inv)
        self.redo_steps.append(step)
        return True

    def redo(self):
        if not self.redo_steps:
            return False
        step = self.redo_steps.pop()
        self.version += 1
        for op, inv in step:
            self.apply(op)
        self.undo_steps.append(step)
        return True
