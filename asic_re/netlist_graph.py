"""Build and summarize a directed NetworkX graph from a KLayout netlist."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re

import networkx as nx

from .extract import extract_netlist
from .verilog import INTERFACES, POWER_PINS, ModuleInterface, is_functional_cell


CELL_RE = re.compile(r"^sky130_fd_sc_hd__(.+)_([0-9]+)$")
PORT_RE = re.compile(
    r"^\s*(input|output)\s+(?:(?:wire|reg)\s+)?([^;]+);",
    flags=re.MULTILINE,
)
SEQUENTIAL_FAMILIES = {"dfrtp", "dfstp", "dfxtp"}


@dataclass(frozen=True)
class CellPorts:
    inputs: frozenset[str]
    outputs: frozenset[str]


def cell_family(cell_type: str) -> str:
    match = CELL_RE.fullmatch(cell_type)
    if match is None:
        raise ValueError(f"Not a supported Sky130 drive-strength cell: {cell_type}")
    return match.group(1)


def load_cell_ports(model_root: Path, cell_types: set[str]) -> dict[str, CellPorts]:
    """Read logical pin directions from the official functional models."""

    result: dict[str, CellPorts] = {}
    for cell_type in sorted(cell_types):
        family = cell_family(cell_type)
        path = model_root / "cells" / family / f"sky130_fd_sc_hd__{family}.functional.v"
        text = path.read_text(encoding="utf-8")
        directions: dict[str, set[str]] = {"input": set(), "output": set()}
        for direction, declaration in PORT_RE.findall(text):
            for pin in declaration.split(","):
                directions[direction].add(pin.strip())
        result[cell_type] = CellPorts(
            inputs=frozenset(directions["input"]),
            outputs=frozenset(directions["output"]),
        )
    return result


def _net_name(net: object) -> str:
    return net.name or net.expanded_name()


def build_graph(
    netlist: object,
    top_cell_name: str,
    interface: ModuleInterface,
    model_root: Path,
) -> nx.MultiDiGraph:
    """Create a bipartite directed graph of cells and electrical nets."""

    top = next(
        (circuit for circuit in netlist.each_circuit() if circuit.name == top_cell_name),
        None,
    )
    if top is None:
        raise ValueError(f"Netlist has no top circuit named {top_cell_name}")

    subcircuits = [
        subcircuit
        for subcircuit in top.each_subcircuit()
        if is_functional_cell(subcircuit.circuit_ref().name)
    ]
    cell_types = {subcircuit.circuit_ref().name for subcircuit in subcircuits}
    ports = load_cell_ports(model_root, cell_types)

    graph = nx.MultiDiGraph(top_cell=top_cell_name)
    for net in top.each_net():
        name = _net_name(net)
        graph.add_node(f"net:{name}", kind="net", name=name)

    for port in interface.inputs:
        graph.add_node(f"port:{port}", kind="input", name=port)
        graph.add_edge(f"port:{port}", f"net:{port}", pin=port)
    for port in interface.outputs:
        graph.add_node(f"port:{port}", kind="output", name=port)
        graph.add_edge(f"net:{port}", f"port:{port}", pin=port)
    for bus, width in interface.output_buses:
        for bit in range(width):
            name = f"{bus}[{bit}]"
            graph.add_node(f"port:{name}", kind="output", name=name)
            graph.add_edge(f"net:{name}", f"port:{name}", pin=name)

    for subcircuit in subcircuits:
        cell = subcircuit.circuit_ref()
        family = cell_family(cell.name)
        cell_node = f"cell:{subcircuit.expanded_name()}"
        graph.add_node(
            cell_node,
            kind="cell",
            name=subcircuit.expanded_name(),
            cell_type=cell.name,
            family=family,
            sequential=family in SEQUENTIAL_FAMILIES,
            x=float(subcircuit.trans.disp.x),
            y=float(subcircuit.trans.disp.y),
        )

        for pin in cell.each_pin():
            pin_name = pin.name()
            if not pin_name or pin_name in POWER_PINS:
                continue
            net = subcircuit.net_for_pin(pin.id())
            if net is None:
                raise ValueError(f"{cell_node}.{pin_name} is unconnected")
            net_node = f"net:{_net_name(net)}"
            if pin_name in ports[cell.name].inputs:
                graph.add_edge(net_node, cell_node, pin=pin_name)
            elif pin_name in ports[cell.name].outputs:
                graph.add_edge(cell_node, net_node, pin=pin_name)
            else:
                raise ValueError(f"No direction for official pin {cell.name}.{pin_name}")

    return graph


def _node_kind(graph: nx.MultiDiGraph, node: str) -> str:
    return graph.nodes[node]["kind"]


def boundary_sources(
    graph: nx.MultiDiGraph,
    start_node: str,
) -> tuple[set[str], set[str], set[str]]:
    """Walk backward through combinational logic, stopping at state and inputs."""

    state: set[str] = set()
    inputs: set[str] = set()
    undriven: set[str] = set()
    pending = [start_node]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        kind = _node_kind(graph, node)
        if kind == "cell" and graph.nodes[node]["sequential"]:
            state.add(node)
            continue
        if kind == "input":
            inputs.add(graph.nodes[node]["name"])
            continue
        predecessors = list(graph.predecessors(node))
        if not predecessors and kind == "net":
            undriven.add(graph.nodes[node]["name"])
        pending.extend(predecessors)
    return state, inputs, undriven


def _input_net_for_pin(graph: nx.MultiDiGraph, cell: str, pin: str) -> str:
    matches = [
        source
        for source, _target, data in graph.in_edges(cell, data=True)
        if data["pin"] == pin
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one {cell}.{pin} input net, found {matches}")
    return matches[0]


def state_dependency_graph(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Collapse combinational D cones into dependencies between flip-flops."""

    state_graph = nx.DiGraph()
    flops = [
        node
        for node, data in graph.nodes(data=True)
        if data["kind"] == "cell" and data["sequential"]
    ]
    for flop in flops:
        state_graph.add_node(flop, **graph.nodes[flop])
    for destination in flops:
        d_net = _input_net_for_pin(graph, destination, "D")
        sources, inputs, undriven = boundary_sources(graph, d_net)
        state_graph.nodes[destination]["input_dependencies"] = sorted(inputs)
        state_graph.nodes[destination]["undriven_dependencies"] = sorted(undriven)
        state_graph.nodes[destination]["d_net"] = graph.nodes[d_net]["name"]
        for source in sources:
            state_graph.add_edge(source, destination)
    return state_graph


def summarize_graph(graph: nx.MultiDiGraph) -> str:
    cells = [node for node in graph if _node_kind(graph, node) == "cell"]
    nets = [node for node in graph if _node_kind(graph, node) == "net"]
    logical_nets = [node for node in nets if graph.degree(node) > 0]
    flops = [node for node in cells if graph.nodes[node]["sequential"]]

    multiple_driver_nets: list[str] = []
    undriven_nets: list[str] = []
    unloaded_nets: list[str] = []
    for net in logical_nets:
        drivers = [node for node in graph.predecessors(net) if _node_kind(graph, node) != "net"]
        loads = [node for node in graph.successors(net) if _node_kind(graph, node) != "net"]
        if len(drivers) > 1:
            multiple_driver_nets.append(graph.nodes[net]["name"])
        if not drivers:
            undriven_nets.append(graph.nodes[net]["name"])
        if not loads:
            unloaded_nets.append(graph.nodes[net]["name"])

    combinational = graph.copy()
    combinational.remove_nodes_from(flops)
    cycles = [
        component
        for component in nx.strongly_connected_components(combinational)
        if len(component) > 1
    ]

    cell_families = Counter(graph.nodes[cell]["family"] for cell in cells)
    flop_families = Counter(graph.nodes[cell]["family"] for cell in flops)
    clock_nets: Counter[str] = Counter()
    for flop in flops:
        for source, _target, data in graph.in_edges(flop, data=True):
            if data["pin"] == "CLK":
                clock_nets[graph.nodes[source]["name"]] += 1

    state_graph = state_dependency_graph(graph)
    state_sccs = sorted(
        (component for component in nx.strongly_connected_components(state_graph)),
        key=len,
        reverse=True,
    )
    input_dependent_flops = Counter(
        input_name
        for _flop, data in state_graph.nodes(data=True)
        for input_name in data["input_dependencies"]
    )

    lines = [
        "Puzzle connectivity summary",
        "===========================",
        f"Functional cells: {len(cells)}",
        f"Logical cell families: {len(cell_families)}",
        f"Electrical nets: {len(nets)}",
        f"Directed pin connections: {graph.number_of_edges()}",
        f"Flip-flops: {len(flops)} ({dict(sorted(flop_families.items()))})",
        f"Clock-net groups: {dict(clock_nets.most_common())}",
        f"Multiple-driver nets: {multiple_driver_nets}",
        f"Undriven nets: {undriven_nets}",
        f"Unloaded nets: {unloaded_nets}",
        f"Combinational cycles: {len(cycles)}",
        f"State-dependency edges: {state_graph.number_of_edges()}",
        f"State SCC sizes: {[len(component) for component in state_sccs]}",
        f"Flop D cones directly influenced by top inputs: {dict(input_dependent_flops)}",
        "",
        "Output cones (unique cells, including sequential state)",
        "------------------------------------------------------",
    ]
    output_nodes = sorted(
        (node for node in graph if _node_kind(graph, node) == "output"),
        key=lambda node: graph.nodes[node]["name"],
    )
    for output in output_nodes:
        ancestors = nx.ancestors(graph, output)
        cone_cells = [node for node in ancestors if _node_kind(graph, node) == "cell"]
        cone_flops = [node for node in cone_cells if graph.nodes[node]["sequential"]]
        lines.append(
            f"{graph.nodes[output]['name']}: {len(cone_cells)} cells, {len(cone_flops)} flops"
        )

    lines.extend(("", "Output sequential boundaries", "----------------------------"))
    for output in output_nodes:
        sources, inputs, undriven = boundary_sources(graph, output)
        source_names = sorted(graph.nodes[source]["name"] for source in sources)
        lines.append(
            f"{graph.nodes[output]['name']}: state={source_names}, "
            f"inputs={sorted(inputs)}, undriven={sorted(undriven)}"
        )

    lines.extend(("", "State SCC placement ranges", "--------------------------"))
    for index, component in enumerate(state_sccs, start=1):
        xs = [state_graph.nodes[node]["x"] for node in component]
        ys = [state_graph.nodes[node]["y"] for node in component]
        lines.append(
            f"SCC {index}: {len(component)} flops, "
            f"x={min(xs):.2f}..{max(xs):.2f}, y={min(ys):.2f}..{max(ys):.2f}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--models",
        type=Path,
        default=Path("third_party/sky130_fd_sc_hd"),
    )
    args = parser.parse_args()

    extracted = extract_netlist(args.gds)
    interface = INTERFACES[extracted.top_cell_name]
    graph = build_graph(extracted.netlist, extracted.top_cell_name, interface, args.models)
    report = summarize_graph(graph)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
