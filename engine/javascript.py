"""
Motor JavaScript para Reidi TGL.

Requiere Node.js 18 o superior.

Los valores exportados a TGL deben ser serializables:
objetos, arrays, strings, números, booleanos o null.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from pathlib import Path

from .errors import TGLRuntimeError


_BRIDGE = r'''
const fs = require("fs");
const vm = require("vm");

const input = JSON.parse(
    fs.readFileSync(0, "utf8")
);

const sandbox = Object.create(null);

sandbox.JSON = JSON;
sandbox.Math = Math;
sandbox.Date = Date;
sandbox.Array = Array;
sandbox.Object = Object;
sandbox.String = String;
sandbox.Number = Number;
sandbox.Boolean = Boolean;
sandbox.RegExp = RegExp;
sandbox.Map = Map;
sandbox.Set = Set;

sandbox.console = Object.freeze({
    log: () => {},
    warn: () => {},
    error: () => {}
});

const context = vm.createContext(
    sandbox,
    {
        codeGeneration: {
            strings: false,
            wasm: false
        }
    }
);

try {
    const names = input.names;

    const wrapped =
        input.source +
        "\n;JSON.stringify({" +
        names.map(
            name =>
                JSON.stringify(name) +
                ": (typeof " +
                name +
                " === 'undefined' ? null : " +
                name +
                ")"
        ).join(",") +
        "})";

    const result = new vm.Script(
        wrapped,
        {
            filename: input.filename || "<tgl-js>"
        }
    ).runInContext(
        context,
        {
            timeout: input.timeout || 1500
        }
    );

    process.stdout.write(
        JSON.stringify({
            ok: true,
            value: JSON.parse(result || "{}")
        })
    );

} catch (error) {

    process.stdout.write(
        JSON.stringify({
            ok: false,
            error: String(error && error.stack || error)
        })
    );

}
'''


_DECLARATION = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
    re.M
)


def execute_javascript(
    source: str,
    *,
    filename: str = "<tgl-js>",
    timeout_ms: int = 1500
) -> dict:

    node = shutil.which("node")

    if not node:
        raise TGLRuntimeError(
            "Para ejecutar JavaScript real, instala Node.js 18 o superior "
            "y asegúrate de que 'node' esté en PATH."
        )

    names = list(
        dict.fromkeys(
            _DECLARATION.findall(source)
        )
    )

    payload = json.dumps({
        "source": source,
        "names": names,
        "filename": filename,
        "timeout": timeout_ms
    })

    try:
        process = subprocess.run(
            [node, "-e", _BRIDGE],
            input=payload,
            text=True,
            capture_output=True,
            timeout=max(3, timeout_ms / 1000 + 2),
            check=False
        )

    except subprocess.TimeoutExpired as exc:
        raise TGLRuntimeError(
            f"El bloque JavaScript excedió el límite de {timeout_ms} ms."
        ) from exc

    if process.returncode != 0:
        raise TGLRuntimeError(
            "Falló el motor JavaScript: " +
            (
                process.stderr.strip()
                or f"código {process.returncode}"
            )
        )

    try:
        result = json.loads(process.stdout)

    except json.JSONDecodeError as exc:
        raise TGLRuntimeError(
            "Node.js devolvió una respuesta JavaScript no válida: " +
            process.stdout[:300]
        ) from exc

    if not result.get("ok"):
        raise TGLRuntimeError(
            "Error JavaScript en " +
            filename +
            ":\n" +
            result.get("error", "Error desconocido")
        )

    values = result.get("value") or {}

    for key, value in values.items():
        try:
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False
            )

        except (TypeError, ValueError) as exc:
            raise TGLRuntimeError(
                f"La variable JavaScript '{key}' "
                "no es serializable a datos TGL."
            ) from exc

    return values


def load_javascript_files(paths: list[Path]) -> dict:
    """
    Ejecuta archivos JavaScript importados en orden
    y combina sus variables exportadas.
    """

    result = {}

    for path in paths:
        result.update(
            execute_javascript(
                path.read_text(encoding="utf-8"),
                filename=str(path)
            )
        )

    return result