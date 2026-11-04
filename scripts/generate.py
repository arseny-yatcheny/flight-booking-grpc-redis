import subprocess
import sys
from pathlib import Path

import grpc_tools


root = Path(__file__).resolve().parents[1]
output = root / "generated"
output.mkdir(exist_ok=True)
subprocess.run(
    [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{root / 'proto'}",
        f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
        f"--python_out={output}",
        f"--grpc_python_out={output}",
        str(root / "proto/flights.proto"),
    ],
    check=True,
)
for path in output.glob("*.py"):
    path.write_text(
        "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")) + "\n"
    )
