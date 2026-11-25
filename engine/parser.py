from dataclasses import dataclass, field
from pathlib import Path
import re
import json

from .errors import TGLSyntaxError


@dataclass
class Program:
    filename: str
    variables: dict = field(default_factory=dict)
    constants: dict = field(default_factory=dict)
    commands: dict = field(default_factory=dict)
    cmdlist: dict = field(default_factory=dict)
    languages: list = field(default_factory=list)
    events: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    js_blocks: list = field(default_factory=list)
    source: str = ""


def _skip(source, index):
    while index < len(source) and source[index].isspace():
        index += 1
    return index


def _read_group(source, index, filename):
    index = _skip(source, index)

    if index >= len(source) or source[index] != "{":
        raise TGLSyntaxError(
            "Se esperaba '{'",
            filename
        )

    start = index
    depth = 0
    quote = None
    escaped = False
    line = source.count("\n", 0, index) + 1

    while index < len(source):
        char = source[index]

        if quote:
            if char == quote and not escaped:
                quote = None

            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False

        else:
            if char in ('"', "'"):
                quote = char

            elif source.startswith("//", index):
                end = source.find("\\\\", index + 2)

                if end < 0:
                    end = source.find("\n", index + 2)

                if end < 0:
                    return source[start + 1:], len(source)

                index = end + 1
                continue

            elif char == "{":
                depth += 1

            elif char == "}":
                depth -= 1

                if depth == 0:
                    return source[start + 1:index], index + 1

        index += 1

    raise TGLSyntaxError(
        "Bloque sin cerrar",
        filename,
        line,
        1
    )


def _split_top(source, separator=","):
    result = []
    start = 0
    depth = 0
    quote = None
    escaped = False
    index = 0

    while index < len(source):
        char = source[index]

        if quote:
            if char == quote and not escaped:
                quote = None

            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False

        else:
            if char in ('"', "'"):
                quote = char

            elif char in "{[(":
                depth += 1

            elif char in "}])":
                depth -= 1

            elif char == separator and depth == 0:
                result.append(source[start:index].strip())
                start = index + 1

        index += 1

    result.append(source[start:].strip())

    return [
        item
        for item in result
        if item
    ]


def _value(source):
    source = source.strip().rstrip(";").strip()

    if not source:
        return None

    if source[0:1] in ('"', "'") and source[-1:] == source[0:1]:
        try:
            if source[0] == '"':
                return json.loads(source)

            return source[1:-1].replace("\\'", "'")

        except Exception:
            return source[1:-1]

    if source == "null":
        return None

    if source == "true":
        return True

    if source == "false":
        return False

    if re.fullmatch(r"-?\d+(?:\.\d+)?", source):
        return float(source) if "." in source else int(source)

    if source.startswith("{") and source.endswith("}"):
        body = source[1:-1]
        result = {}

        for part in _split_top(body, ";"):
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = _value(value)

        return result

    if source.startswith("[") and source.endswith("]"):
        return [
            _value(item)
            for item in _split_top(source[1:-1])
        ]

    return source


def _fields(body):
    """
    Lee campos de comandos:

        txt = {"texto"}, {id};
        btn = {"botón"}, {null}, {inline}, {acción};
        answer = {"respuesta"}, {null}, {acción};

    Respeta los bloques anidados.
    """

    fields = []
    index = 0
    length = len(body)

    pattern = re.compile(
        r"\s*([A-Za-z_][\w]*)\s*=\s*"
    )

    while index < length:
        index = _skip(body, index)

        if index >= length:
            break

        match = pattern.match(body, index)

        if not match:
            next_line = body.find("\n", index)

            if next_line < 0:
                break

            index = next_line + 1
            continue

        name = match.group(1)
        index = match.end()
        groups = []

        while index < length:
            index = _skip(body, index)

            if index >= length:
                break

            if body[index] == "{":
                group, index = _read_group(
                    body,
                    index,
                    "<field>"
                )

                groups.append(group)

            else:
                start = index
                depth = 0
                quote = None

                while index < length:
                    char = body[index]

                    if quote:
                        if char == quote:
                            quote = None

                    elif char in ('"', "'"):
                        quote = char

                    elif char in "{[(":
                        depth += 1

                    elif char in "}])":
                        if depth == 0:
                            break

                        depth -= 1

                    elif char == ";" and depth == 0:
                        break

                    index += 1

                groups.append(body[start:index].strip())

            index = _skip(body, index)

            if index < length and body[index] == ",":
                index += 1
                continue

            break

        fields.append((name, groups))

        while index < length and body[index] in ";, \t\r\n":
            index += 1

    return fields


class Parser:

    def __init__(self, source, filename="<memory>"):
        self.source = str(source)
        self.filename = str(filename)
        self.program = Program(self.filename)

    def parse(self):
        source = self.source

        self.program.source = source

        index = 0
        length = len(source)

        base = Path(self.filename).resolve().parent

        while index < length:
            index = _skip(source, index)

            if index >= length:
                break

            if source.startswith("//", index):
                end = source.find("\\\\", index + 2)

                index = end + 2 if end >= 0 else length
                continue

            match = re.match(
                r"(import|var|const|cmdlist|lang|cmd|js|on|if|while)\b",
                source[index:]
            )

            if not match:
                if source[index] == ";":
                    index += 1
                    continue

                line = source.count("\n", 0, index) + 1

                raise TGLSyntaxError(
                    f"Instrucción no reconocida cerca de: {source[index:index + 35]!r}",
                    self.filename,
                    line,
                    1
                )

            kind = match.group(1)

            index += len(kind)
            index = _skip(source, index)

            # --------------------------------------------------
            # IMPORTACIONES
            # --------------------------------------------------

            if kind == "import":
                match = re.match(
                    r'''(?:"([^"]+)"|'([^']+)'|([^\s;]+))\s*;?''',
                    source[index:]
                )

                if not match:
                    raise TGLSyntaxError(
                        "Importación no válida",
                        self.filename
                    )

                relative = next(
                    value
                    for value in match.groups()
                    if value is not None
                )

                target = (base / relative).resolve()

                if not target.is_file():
                    raise TGLSyntaxError(
                        f"No existe el archivo importado: {target}",
                        self.filename,
                        source.count("\n", 0, index) + 1,
                        1
                    )

                if target.suffix.lower() == ".js":
                    self.program.js_blocks.append(
                        target.read_text(encoding="utf-8")
                    )

                elif target.suffix.lower() in (
                    ".txt",
                    ".ini",
                    ".env"
                ):
                    for line in target.read_text(
                        encoding="utf-8"
                    ).splitlines():

                        line = line.strip()

                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)

                            self.program.variables[key.strip()] = _value(
                                value
                            )

                else:
                    raise TGLSyntaxError(
                        "Solo se pueden importar archivos .js, .txt, .ini o .env",
                        self.filename
                    )

                index += match.end()

            # --------------------------------------------------
            # VARIABLES
            # --------------------------------------------------

            elif kind == "var":

                if source.startswith("{", index):
                    group, index = _read_group(
                        source,
                        index,
                        self.filename
                    )

                    match = re.match(
                        r"\s*import\s+(.+?)\s*$",
                        group,
                        re.S
                    )

                    if not match:
                        raise TGLSyntaxError(
                            "Usa var {import archivo}",
                            self.filename
                        )

                    path = (
                        base /
                        match.group(1).strip().strip('"\'')
                    ).resolve()

                    if not path.is_file():
                        raise TGLSyntaxError(
                            f"No existe el archivo de variables: {path}",
                            self.filename
                        )

                    for line in path.read_text(
                        encoding="utf-8"
                    ).splitlines():

                        line = line.strip()

                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)

                            self.program.variables[key.strip()] = _value(
                                value
                            )

                else:
                    match = re.match(
                        r"([A-Za-z_]\w*)\s*=\s*",
                        source[index:]
                    )

                    if not match:
                        raise TGLSyntaxError(
                            "Declaración var no válida",
                            self.filename
                        )

                    name = match.group(1)

                    index += match.end()

                    end = source.find(";", index)

                    if end < 0:
                        end = source.find("\n", index)

                    if end < 0:
                        end = length

                    self.program.variables[name] = _value(
                        source[index:end]
                    )

                    index = end + 1

            # --------------------------------------------------
            # CONSTANTES
            # --------------------------------------------------

            elif kind == "const":
                match = re.match(
                    r"([A-Za-z_]\w*)\s*=\s*",
                    source[index:]
                )

                if not match:
                    raise TGLSyntaxError(
                        "Declaración const no válida",
                        self.filename
                    )

                name = match.group(1)

                index += match.end()

                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                obj = {}

                for part in _split_top(group, ";"):
                    part = part.strip()

                    if not part:
                        continue

                    if "=" in part:
                        key, value = part.split("=", 1)

                        obj[key.strip()] = _value(value)

                    else:
                        obj[part] = True

                self.program.constants[name] = obj

                if index < length and source[index] == ";":
                    index += 1

            # --------------------------------------------------
            # LISTA DE COMANDOS
            # --------------------------------------------------

            elif kind == "cmdlist":
                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                pattern = re.finditer(
                    r"""(["'])(.*?)\1\s*:\s*(["'])(.*?)\3""",
                    group
                )

                for match in pattern:
                    self.program.cmdlist[match.group(2)] = match.group(4)

            # --------------------------------------------------
            # IDIOMAS
            # --------------------------------------------------

            elif kind == "lang":
                if index >= length or source[index] != "=":
                    raise TGLSyntaxError(
                        "Se esperaba '=' después de lang",
                        self.filename
                    )

                index += 1

                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                self.program.languages = [
                    _value(value)
                    for value in _split_top(group)
                ]

            # --------------------------------------------------
            # COMANDOS
            # --------------------------------------------------

            elif kind == "cmd":
                match = re.match(
                    r"([A-Za-z_]\w*)\s*=\s*",
                    source[index:]
                )

                if not match:
                    raise TGLSyntaxError(
                        "Comando sin nombre o '='",
                        self.filename
                    )

                name = match.group(1)

                index += match.end()

                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                command = {
                    "name": name,
                    "txt": [],
                    "btn": [],
                    "file": [],
                    "answer": [],
                    "raw": group
                }

                for field_name, groups in _fields(group):

                    if field_name not in (
                        "txt",
                        "btn",
                        "file",
                        "answer"
                    ):
                        continue

                    values = [
                        _value(value)
                        for value in groups
                    ]

                    if field_name == "txt":
                        command["txt"].append({
                            "text": values[0] if values else "",
                            "id": values[1] if len(values) > 1 else None
                        })

                    elif field_name == "file":
                        command["file"].append({
                            "path": values[0] if values else "",
                            "id": values[1] if len(values) > 1 else None
                        })

                    elif field_name == "btn":
                        command["btn"].append({
                            "text": values[0] if values else "",
                            "id": values[1] if len(values) > 1 else None,
                            "type": values[2] if len(values) > 2 else "inline",
                            "actions": groups[3:]
                        })

                    elif field_name == "answer":
                        # answer = {texto}, {id opcional}, {acción}, ...
                        action_start = (
                            2
                            if len(groups) >= 3 and values[1] is None
                            else 1
                        )

                        command["answer"].append({
                            "text": values[0] if values else "",
                            "actions": groups[action_start:]
                        })

                if command["btn"] and not command["txt"]:
                    raise TGLSyntaxError(
                        f"El comando '{name}' tiene botones, pero necesita al menos un txt.",
                        self.filename
                    )

                if name in self.program.commands:
                    raise TGLSyntaxError(
                        f"Comando duplicado: {name}",
                        self.filename
                    )

                self.program.commands[name] = command

            # --------------------------------------------------
            # JAVASCRIPT
            # --------------------------------------------------

            elif kind == "js":
                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                self.program.js_blocks.append(group)

            # --------------------------------------------------
            # EVENTOS DE TELEGRAM
            # --------------------------------------------------

            elif kind == "on":
                header = re.match(
                    r"@user\s+(join|leave)\s*",
                    source[index:]
                )

                if not header:
                    raise TGLSyntaxError(
                        "Evento permitido: on @user join / leave",
                        self.filename
                    )

                event = header.group(1)

                index += header.end()

                group, index = _read_group(
                    source,
                    index,
                    self.filename
                )

                self.program.events.append({
                    "event": event,
                    "body": group
                })

            # --------------------------------------------------
            # REGLAS GLOBALES IF / WHILE
            # --------------------------------------------------

            elif kind in ("if", "while"):
                start = index - len(kind)

                header_end = source.find("{", index)
                line_end = source.find("\n", index)

                if header_end >= 0 and (
                    line_end < 0 or header_end < line_end
                ):
                    body, after = _read_group(
                        source,
                        header_end,
                        self.filename
                    )

                    condition = source[index:header_end].strip()

                    self.program.rules.append({
                        "kind": kind,
                        "condition": condition,
                        "body": body,
                        "raw": source[start:after].strip()
                    })

                    index = after

                else:
                    end = line_end if line_end >= 0 else length

                    self.program.rules.append({
                        "kind": kind,
                        "condition": source[index:end].strip(),
                        "body": "",
                        "raw": source[start:end].strip()
                    })

                    index = end + 1

        return self.program


def parse_file(path):
    path = Path(path).resolve()

    return Parser(
        path.read_text(encoding="utf-8"),
        path
    ).parse()