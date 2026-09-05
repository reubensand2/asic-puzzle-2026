"""Extract a hierarchical electrical netlist from a Sky130 GDS layout."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import klayout.db as db

from .verilog import INTERFACES, emit_structural_verilog


# Sky130 routing conductors and the cut layers that join them. The values are
# GDS (layer, datatype) pairs from the official Sky130 layer reference.
ROUTING_LAYERS = {
    "li1": (67, 20),
    "mcon": (67, 44),
    "met1": (68, 20),
    "via": (68, 44),
    "met2": (69, 20),
    "via2": (69, 44),
    "met3": (70, 20),
    "via3": (70, 44),
    "met4": (71, 20),
    "via4": (71, 44),
    "met5": (72, 20),
}

ROUTING_STACK = (
    ("li1", "mcon", "met1"),
    ("met1", "via", "met2"),
    ("met2", "via2", "met3"),
    ("met3", "via3", "met4"),
    ("met4", "via4", "met5"),
)

# Sky130 uses datatype 5 for labels on each conductor's GDS layer.
LABEL_LAYERS = {
    conductor: (ROUTING_LAYERS[conductor][0], 5)
    for conductor in ("li1", "met1", "met2", "met3", "met4", "met5")
}


@dataclass
class ExtractedNetlist:
    """Keep the related KLayout objects alive and available to later stages."""

    layout: db.Layout
    l2n: db.LayoutToNetlist
    netlist: db.Netlist
    top_cell_name: str


def _required_layer(layout: db.Layout, name: str, spec: tuple[int, int]) -> int:
    index = layout.find_layer(*spec)
    if index is None:
        raise ValueError(f"Required Sky130 layer {name} ({spec[0]}/{spec[1]}) is absent")
    return index


def extract_netlist(gds_path: str | Path) -> ExtractedNetlist:
    """Run KLayout L2N over the complete Sky130 interconnect stack."""

    layout = db.Layout()
    layout.read(str(Path(gds_path)))

    top_cells = tuple(layout.top_cells())
    if len(top_cells) != 1:
        names = ", ".join(cell.name for cell in top_cells)
        raise ValueError(f"Expected one top cell, found {len(top_cells)}: {names}")
    top = top_cells[0]

    # The recursive iterator preserves the GDS hierarchy and applies every
    # instance's translation/rotation/mirroring inside KLayout.
    iterator = db.RecursiveShapeIterator(layout, top, [])
    l2n = db.LayoutToNetlist(iterator)

    layers: dict[str, db.Region] = {}
    for name, spec in ROUTING_LAYERS.items():
        layers[name] = l2n.make_polygon_layer(
            _required_layer(layout, name, spec),
            name,
        )
        l2n.connect(layers[name])

    # A cut joins a conductor below it to the conductor above it only where
    # their polygons overlap.
    for lower, cut, upper in ROUTING_STACK:
        l2n.connect(layers[lower], layers[cut])
        l2n.connect(layers[cut], layers[upper])

    # Labels are optional: some metal levels have no labels in a particular
    # design. When present, attaching them names the corresponding nets/pins.
    for conductor, spec in LABEL_LAYERS.items():
        label_index = layout.find_layer(*spec)
        if label_index is None:
            continue
        labels = l2n.make_text_layer(label_index, f"{conductor}_labels")
        l2n.connect(layers[conductor], labels)

    l2n.extract_netlist()
    netlist = l2n.netlist()
    # if netlist is None:
    #     raise RuntimeError("KLayout completed extraction without producing a netlist")

    return ExtractedNetlist(
        layout=layout,
        l2n=l2n,
        netlist=netlist,
        top_cell_name=top.name,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path, help="Sky130 GDS file to extract")
    parser.add_argument("output", type=Path, help="KLayout .l2n database to write")
    parser.add_argument(
        "--include-power-pins",
        action="store_true",
        help="include supply connections in structural Verilog",
    )
    args = parser.parse_args()

    extracted = extract_netlist(args.gds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    extracted.l2n.write(str(args.output))

    text_path = args.output.with_suffix(".txt")
    text_path.write_text(str(extracted.netlist) + "\n", encoding="utf-8")
    verilog_path = args.output.with_suffix(".v")
    interface = INTERFACES.get(extracted.top_cell_name)
    if interface is not None:
        verilog_path.write_text(
            emit_structural_verilog(
                extracted.netlist,
                extracted.top_cell_name,
                interface,
                include_power_pins=args.include_power_pins,
            ),
            encoding="utf-8",
        )
    circuit_count = sum(1 for _ in extracted.netlist.each_circuit())

    print(f"Top cell: {extracted.top_cell_name}")
    print(f"Extracted circuits: {circuit_count}")
    print(f"KLayout database: {args.output}")
    print(f"Text netlist: {text_path}")
    if interface is not None:
        print(f"Structural Verilog: {verilog_path}")


if __name__ == "__main__":
    main()
