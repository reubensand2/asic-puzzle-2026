"""Emit structural Verilog from KLayout's extracted netlist."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

import klayout.db as db


PHYSICAL_CELL_MARKERS = ("__decap_", "__fill_", "__filler_", "__tap", "__diode_")
POWER_PINS = {"VPWR", "VGND", "VPB", "VNB"}


@dataclass(frozen=True)
class ModuleInterface:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    output_buses: tuple[tuple[str, int], ...] = ()

    @property
    def ports(self) -> tuple[str, ...]:
        return self.inputs + self.outputs + tuple(name for name, _ in self.output_buses)

    @property
    def net_names(self) -> set[str]:
        names = set(self.inputs + self.outputs)
        for bus, width in self.output_buses:
            names.update(f"{bus}[{bit}]" for bit in range(width))
        return names


INTERFACES = {
    "adder_demo": ModuleInterface(
        inputs=("A", "B", "clk", "en", "rst_n"),
        outputs=("S",),
    ),
    "puzzle": ModuleInterface(
        inputs=("clk", "rst_n", "enable", "I"),
        outputs=("success",),
        output_buses=(("O", 8),),
    ),
}


def is_functional_cell(cell_type: str) -> bool:
    return cell_type.startswith("sky130_fd_sc_hd__") and not any(
        marker in cell_type for marker in PHYSICAL_CELL_MARKERS
    )


def _safe_identifier(value: str, prefix: str) -> str:
    value = value.lstrip("$")
    value = re.sub(r"[^A-Za-z0-9_$]", "_", value)
    return f"{prefix}_{value}" if value else prefix


def _net_key(net: db.Net) -> str:
    return net.name or net.expanded_name()


def _find_top_circuit(netlist: db.Netlist, top_cell_name: str) -> db.Circuit:
    for circuit in netlist.each_circuit():
        if circuit.name == top_cell_name:
            return circuit
    raise ValueError(f"Netlist has no top circuit named {top_cell_name}")


def _functional_subcircuits(circuit: db.Circuit) -> Iterable[db.SubCircuit]:
    for subcircuit in circuit.each_subcircuit():
        if is_functional_cell(subcircuit.circuit_ref().name):
            yield subcircuit


def emit_structural_verilog(
    netlist: db.Netlist,
    top_cell_name: str,
    interface: ModuleInterface,
    include_power_pins: bool = False,
) -> str:
    """Translate one extracted top circuit into named-pin cell instances.

    By default this emits a logic-only view for use with the Sky130 functional
    models. The KLayout netlist still retains the physical power connectivity.
    """

    top = _find_top_circuit(netlist, top_cell_name)
    top_nets = tuple(top.each_net())
    available_net_names = {_net_key(net) for net in top_nets}
    missing_ports = interface.net_names - available_net_names
    if missing_ports:
        missing = ", ".join(sorted(missing_ports))
        raise ValueError(f"Extracted top circuit is missing interface nets: {missing}")

    net_names: dict[str, str] = {}
    reserved_identifiers = set(interface.net_names) | POWER_PINS
    used_identifiers: set[str] = set()
    for net in top_nets:
        key = _net_key(net)
        if key in reserved_identifiers:
            identifier = key
        else:
            identifier = _safe_identifier(key, "n")
        while identifier in used_identifiers or (
            key not in reserved_identifiers and identifier in reserved_identifiers
        ):
            identifier += "_dup"
        used_identifiers.add(identifier)
        net_names[key] = identifier

    lines = [f"module {top_cell_name} (", "    " + ",\n    ".join(interface.ports), ");"]
    for port in interface.inputs:
        lines.append(f"  input {port};")
    for port in interface.outputs:
        lines.append(f"  output {port};")
    for bus, width in interface.output_buses:
        lines.append(f"  output [{width - 1}:0] {bus};")
    lines.append("")
    if include_power_pins:
        lines.extend(("  supply1 VPWR;", "  supply0 VGND;", ""))

    internal_nets = sorted(
        identifier
        for identifier in net_names.values()
        if identifier not in interface.net_names and identifier not in POWER_PINS
    )
    lines.extend(f"  wire {identifier};" for identifier in internal_nets)
    lines.append("")

    for subcircuit in _functional_subcircuits(top):
        cell = subcircuit.circuit_ref()
        instance_name = _safe_identifier(subcircuit.expanded_name(), "u")

        connections: list[str] = []
        for pin in cell.each_pin():
            # L2N can expose an unlabeled conductor crossing a cell boundary as
            # an anonymous physical terminal. It is not part of the official
            # standard-cell module interface, so it has no Verilog port.
            if not pin.name():
                continue
            if not include_power_pins and pin.name() in POWER_PINS:
                continue
            net = subcircuit.net_for_pin(pin.id())
            if net is None:
                connections.append(f".{pin.name()}()")
            else:
                connections.append(f".{pin.name()}({net_names[_net_key(net)]})")

        lines.append(f"  {cell.name} {instance_name} (")
        lines.append("      " + ",\n      ".join(connections))
        lines.append("  );")

    lines.extend(("", "endmodule", ""))
    return "\n".join(lines)
