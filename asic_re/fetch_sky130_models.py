"""Download the official Sky130 functional models used by a Verilog netlist."""

from __future__ import annotations

import argparse
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "google/skywater-pdk-libs-sky130_fd_sc_hd"
GITHUB_API = f"https://api.github.com/repos/{REPOSITORY}"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}"
CELL_MODULE_RE = re.compile(
    r"^\s*(sky130_fd_sc_hd__([A-Za-z0-9_]+)_([0-9]+))\s+"
    r"[A-Za-z_$][A-Za-z0-9_$]*\s*\(",
    flags=re.MULTILINE,
)
INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', flags=re.MULTILINE)


def cell_modules_in_verilog(text: str) -> dict[str, str]:
    """Return exact standard-cell module names mapped to their cell family."""

    return {module: family for module, family, _drive in CELL_MODULE_RE.findall(text)}


def _read_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "asic-puzzle-model-fetcher"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"Could not download {url}: HTTP {error.code}") from error


def _resolve_revision(revision: str) -> str:
    url = f"{GITHUB_API}/commits/{quote(revision, safe='')}"
    response = json.loads(_read_url(url))
    return response["sha"]


def _source_path(current_file: PurePosixPath, include: str) -> PurePosixPath:
    normalized = posixpath.normpath(posixpath.join(str(current_file.parent), include))
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError(f"Include escapes the Sky130 source tree: {include}")
    return PurePosixPath(normalized)


def download_models(
    verilog_paths: list[Path],
    output_dir: Path,
    revision: str = "main",
) -> tuple[set[str], list[PurePosixPath], str]:
    """Download wrappers, selectors, functional models, and referenced UDPs."""

    modules: dict[str, str] = {}
    for verilog_path in verilog_paths:
        modules.update(cell_modules_in_verilog(verilog_path.read_text(encoding="utf-8")))
    if not modules:
        raise ValueError("No sky130_fd_sc_hd standard-cell instances were found")

    commit = _resolve_revision(revision)
    downloaded: dict[PurePosixPath, str] = {}

    def fetch(source_path: PurePosixPath) -> str:
        if source_path not in downloaded:
            url = f"{RAW_ROOT}/{commit}/{quote(str(source_path), safe='/')}"
            text = _read_url(url).decode("utf-8")
            destination = output_dir.joinpath(*source_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8", newline="")
            downloaded[source_path] = text
        return downloaded[source_path]

    for module, family in sorted(modules.items()):
        cell_dir = PurePosixPath("cells") / family
        fetch(cell_dir / f"{module}.v")
        fetch(cell_dir / f"sky130_fd_sc_hd__{family}.v")
        functional_path = cell_dir / f"sky130_fd_sc_hd__{family}.functional.v"
        functional_text = fetch(functional_path)

        # Functional sequential models include a UDP by a relative path. UDPs
        # are self-contained for normal simulation (NO_PRIMITIVES is unset).
        for include in INCLUDE_RE.findall(functional_text):
            fetch(_source_path(functional_path, include))

    license_text = _read_url(f"{RAW_ROOT}/{commit}/LICENSE").decode("utf-8")
    (output_dir / "LICENSE").write_text(license_text, encoding="utf-8", newline="")

    source_lines = [
        f"Repository: https://github.com/{REPOSITORY}",
        f"Requested revision: {revision}",
        f"Resolved commit: {commit}",
        "",
        "Cell modules:",
        *(f"- {module}" for module in sorted(modules)),
        "",
        "Downloaded files:",
        *(f"- {path}" for path in sorted(downloaded, key=str)),
        "- LICENSE",
        "",
    ]
    (output_dir / "SOURCE.txt").write_text("\n".join(source_lines), encoding="utf-8")
    return set(modules), sorted(downloaded, key=str), commit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verilog", type=Path, nargs="+", help="structural Verilog input")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("third_party/sky130_fd_sc_hd"),
        help="destination for the selected upstream files",
    )
    parser.add_argument("--revision", default="main", help="upstream branch, tag, or commit")
    args = parser.parse_args()

    modules, files, commit = download_models(args.verilog, args.output, args.revision)
    print(f"Sky130 commit: {commit}")
    print(f"Cell modules: {len(modules)}")
    print(f"Downloaded source files: {len(files)}")
    print(f"Destination: {args.output}")


if __name__ == "__main__":
    main()
