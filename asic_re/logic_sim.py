"""Cycle-level Boolean evaluation of the recovered Sky130 cell graph."""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from operator import xor
from typing import Any

import networkx as nx
import z3


@dataclass(frozen=True)
class Flop:
    node: str
    family: str
    q_net: str
    d_net: str


@dataclass
class CompiledCircuit:
    """A graph with its static evaluation order computed once."""

    graph: nx.MultiDiGraph
    flops: list[Flop]
    combinational: nx.MultiDiGraph
    order: list[str]
    source_nets: list[str]

    @classmethod
    def from_graph(cls, graph: nx.MultiDiGraph) -> "CompiledCircuit":
        flops = flops_in_graph(graph)
        combinational = graph.copy()
        combinational.remove_nodes_from(flop.node for flop in flops)
        order = [
            node
            for node in nx.topological_sort(combinational)
            if combinational.nodes[node]["kind"] == "cell"
        ]
        source_nets = [
            node
            for node, data in combinational.nodes(data=True)
            if data["kind"] == "net" and combinational.in_degree(node) == 0
        ]
        return cls(graph, flops, combinational, order, source_nets)

    def evaluate(self, state: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        values = dict(state)
        for name, value in inputs.items():
            values[f"net:{name}"] = value
        for node in self.source_nets:
            values.setdefault(node, False)

        for node in self.order:
            data = self.combinational.nodes[node]
            pin_values = {
                edge["pin"]: values[source]
                for source, _target, edge in self.combinational.in_edges(node, data=True)
            }
            outputs = evaluate_cell(data["family"], pin_values)
            for _source, target, edge in self.combinational.out_edges(node, data=True):
                values[target] = outputs[edge["pin"]]
        return values

    def step(self, state: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        values = self.evaluate(state, inputs)
        return {flop.q_net: values[flop.d_net] for flop in self.flops}

    def outputs(self, state: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        values = self.evaluate(state, inputs)
        outputs: dict[str, Any] = {}
        for node, data in self.graph.nodes(data=True):
            if data["kind"] == "output":
                net = next(self.graph.predecessors(node))
                outputs[data["name"]] = values[net]
        return outputs


def _not(value: Any) -> Any:
    return not value if isinstance(value, bool) else z3.Not(value)


def _and(values: list[Any]) -> Any:
    if all(isinstance(value, bool) for value in values):
        return all(values)
    return z3.And(*values)


def _or(values: list[Any]) -> Any:
    if all(isinstance(value, bool) for value in values):
        return any(values)
    return z3.Or(*values)


def _xor(values: list[Any]) -> Any:
    if all(isinstance(value, bool) for value in values):
        return reduce(xor, values)
    return z3.Xor(*values)


def evaluate_cell(family: str, pins: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one combinational Sky130 family from its named input pins."""

    values = {
        pin: _not(value) if pin.endswith("_N") else value
        for pin, value in pins.items()
    }

    if family == "conb":
        return {"HI": True, "LO": False}
    if family in {"buf", "clkbuf"}:
        return {"X": values["A"]}
    if family == "inv":
        return {"Y": _not(values["A"])}
    if family == "mux2":
        select = values["S"]
        if isinstance(select, bool):
            result = values["A1"] if select else values["A0"]
        else:
            result = z3.If(select, values["A1"], values["A0"])
        return {"X": result}
    if family.startswith("xnor"):
        return {"Y": _not(_xor(list(values.values())))}
    if family.startswith("xor"):
        return {"X": _xor(list(values.values()))}

    for prefix, combine, invert, output in (
        ("nand", _and, True, "Y"),
        ("nor", _or, True, "Y"),
        ("and", _and, False, "X"),
        ("or", _or, False, "X"),
    ):
        if family.startswith(prefix):
            result = combine(list(values.values()))
            return {output: _not(result) if invert else result}

    if family.startswith(("a", "o")):
        groups: dict[str, list[Any]] = {}
        for pin, value in values.items():
            groups.setdefault(pin[0], []).append(value)
        if family.startswith("a"):
            result = _or([_and(group) for group in groups.values()])
            output = "Y" if family.endswith("i") else "X"
        else:
            result = _and([_or(group) for group in groups.values()])
            output = "Y" if family.endswith("i") else "X"
        return {output: _not(result) if family.endswith("i") else result}

    raise ValueError(f"Unsupported combinational cell family: {family}")


def flops_in_graph(graph: nx.MultiDiGraph) -> list[Flop]:
    result: list[Flop] = []
    for node, data in graph.nodes(data=True):
        if data["kind"] != "cell" or not data["sequential"]:
            continue
        q_nets = [
            target
            for _source, target, edge in graph.out_edges(node, data=True)
            if edge["pin"] == "Q"
        ]
        d_nets = [
            source
            for source, _target, edge in graph.in_edges(node, data=True)
            if edge["pin"] == "D"
        ]
        if len(q_nets) != 1 or len(d_nets) != 1:
            raise ValueError(f"Could not identify Q/D nets for {node}")
        result.append(Flop(node, data["family"], q_nets[0], d_nets[0]))
    return result


def initial_state(graph: nx.MultiDiGraph, symbolic_unreset: bool = False) -> dict[str, Any]:
    """Return state after rst_n has been asserted low."""

    state: dict[str, Any] = {}
    for flop in flops_in_graph(graph):
        if flop.family == "dfstp":
            state[flop.q_net] = True
        elif flop.family == "dfxtp" and symbolic_unreset:
            state[flop.q_net] = z3.Bool(f"initial_{graph.nodes[flop.node]['name'][1:]}")
        else:
            state[flop.q_net] = False
    return state


def evaluate_combinational(
    graph: nx.MultiDiGraph,
    state: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all combinational cells for fixed input and Q values."""

    return CompiledCircuit.from_graph(graph).evaluate(state, inputs)


def step(
    graph: nx.MultiDiGraph,
    state: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate D logic and capture every flip-flop on one rising edge."""

    return CompiledCircuit.from_graph(graph).step(state, inputs)


def output_values(
    graph: nx.MultiDiGraph,
    state: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return CompiledCircuit.from_graph(graph).outputs(state, inputs)
