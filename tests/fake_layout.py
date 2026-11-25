# SPDX-License-Identifier: Apache-2.0
"""A stand-in for bpy.types.UILayout that records what a panel draws.

There is no UI headless, so panel tests drive draw code with this instead. It does more than
swallow calls: it checks what a real layout would reject -- property paths that do not
resolve, search collections that do not exist, operators that are not registered, operator
properties that the operator does not have -- so a typo fails the test instead of the sidebar.
"""

import bpy


class Recorder:
    def __init__(self):
        self.calls = []

    def texts(self):
        """Every label and button text drawn, in order."""
        return [kw.get("text", "") for name, args, kw in self.calls
                if name in ("label", "operator", "prop", "prop_search")]

    def operators(self):
        return [args[0] for name, args, kw in self.calls if name == "operator"]

    def props(self):
        """(data, property path) of every property row drawn."""
        return [(args[0], args[1]) for name, args, kw in self.calls if name in ("prop", "prop_search")]


def _check_prop(data, prop):
    if prop.startswith('["'):
        key = prop[2:-2]
        assert key in data.keys(), f"{data!r} has no ID property {key}"
    else:
        assert prop in data.bl_rna.properties, f"{data!r} has no property {prop}"


class OperatorProps:
    def __init__(self, idname):
        object.__setattr__(self, "_idname", idname)
        module, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
        object.__setattr__(self, "_names", {p.identifier for p in rna.properties})

    def __setattr__(self, name, value):
        assert name in self._names, f"{self._idname} has no property {name}"


class Layout:
    def __init__(self, recorder=None):
        object.__setattr__(self, "_rec", recorder or Recorder())

    @property
    def recorder(self):
        return self._rec

    def __setattr__(self, name, value):
        self._rec.calls.append(("set", (name, value), {}))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self._rec.calls.append((name, args, kwargs))
            if name == "panel":
                return Layout(self._rec), Layout(self._rec)
            if name == "operator":
                module, op = args[0].split(".")
                assert hasattr(getattr(bpy.ops, module), op), f"operator {args[0]} not registered"
                return OperatorProps(args[0])
            if name == "prop":
                _check_prop(args[0], args[1])
            if name == "prop_search":
                _check_prop(args[0], args[1])
                assert hasattr(args[2], args[3]), f"{args[2]!r} has no collection {args[3]}"
            return Layout(self._rec)
        return call
