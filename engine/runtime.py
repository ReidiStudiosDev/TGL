"""Runtime for Reidi Telegram Language (TGL)."""

from __future__ import annotations

import ast
import asyncio
import json
import operator
import random
import re
import sqlite3
import uuid

from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import TGLRuntimeError
from .javascript import execute_javascript


_COMPARE = {
    "=": operator.eq,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
}

_ARITHMETIC = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _split_top(text: str, separators=",;\n") -> list[str]:
    """Separa expresiones ignorando cadenas y bloques anidados."""

    result = []
    start = 0
    depth = 0
    quote = None
    escaped = False

    for index, char in enumerate(text):

        if quote:
            if char == quote and not escaped:
                quote = None

            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False

            continue

        if char in ("'", '"'):
            quote = char

        elif char in "{[(":
            depth += 1

        elif char in "}])":
            depth = max(0, depth - 1)

        elif char in separators and depth == 0:
            part = text[start:index].strip()

            if part:
                result.append(part)

            start = index + 1

    tail = text[start:].strip()

    if tail:
        result.append(tail)

    return result


def _find_matching(
    text: str,
    start: int,
    opening="{",
    closing="}"
) -> int:

    if start >= len(text) or text[start] != opening:
        raise TGLRuntimeError(
            f"Se esperaba '{opening}' en una acción."
        )

    depth = 0
    quote = None
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if quote:
            if char == quote and not escaped:
                quote = None

            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False

        elif char in ("'", '"'):
            quote = char

        elif char == opening:
            depth += 1

        elif char == closing:
            depth -= 1

            if depth == 0:
                return index

    raise TGLRuntimeError(
        "Bloque de acción sin cerrar."
    )


def _extract_braced(text: str, start: int) -> tuple[str, int]:

    start = text.find("{", start)

    if start < 0:
        return "", len(text)

    end = _find_matching(text, start)

    return text[start + 1:end], end + 1


def _literal(value: str) -> Any:

    value = value.strip()

    if not value:
        return None

    if (
        len(value) >= 2
        and value[0] in ("'", '"')
        and value[-1] == value[0]
    ):
        try:
            return ast.literal_eval(value)

        except Exception:
            return value[1:-1]

    if value == "true":
        return True

    if value == "false":
        return False

    if value == "null":
        return None

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)

    return value


def _split_boolean(text: str, keyword: str) -> list[str]:
    """Divide condiciones por AND/OR sin romper paréntesis ni cadenas."""

    parts = []
    start = 0
    depth = 0
    quote = None
    escaped = False

    pattern = re.compile(
        rf"\s+{re.escape(keyword)}\s+",
        re.I
    )

    index = 0

    while index < len(text):
        char = text[index]

        if quote:
            if char == quote and not escaped:
                quote = None

            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False

            index += 1
            continue

        if char in ("'", '"'):
            quote = char

        elif char == "(":
            depth += 1

        elif char == ")":
            depth = max(0, depth - 1)

        elif depth == 0:
            match = pattern.match(text, index)

            if match:
                parts.append(
                    text[start:index].strip()
                )

                index = match.end()
                start = index
                continue

        index += 1

    if parts:
        parts.append(text[start:].strip())
        return parts

    return [text.strip()]


class Runtime:

    def __init__(self, program):

        self.p = program

        self.path = Path(
            program.filename
        ).expanduser().resolve()

        self.base = self.path.parent

        self.data = dict(program.variables)

        # Ejecutar los bloques JS e importar sus variables.
        if program.js_blocks:
            self.data.update(
                execute_javascript(
                    "\n".join(program.js_blocks),
                    filename=str(self.path) + "::<js>"
                )
            )

        self.users: dict[int, dict] = {}

        self.message_ids: dict[
            tuple[int, str],
            int
        ] = {}

        self.pending: dict[
            tuple[int, int],
            dict
        ] = {}

        self.keyboard_actions: dict[
            tuple[int, int, str],
            str
        ] = {}

        self.callback_actions: dict[str, str] = {}

        self.tasks: set[asyncio.Task] = set()

        self.app = None

        self.token = (
            self.data.get("BOT_TOKEN")
            or self.data.get("TELEGRAM_BOT")
        )

        self.db_path = (
            self.base / ".tgl_data.sqlite3"
        )

        self._init_storage()

        self.bot_username = ""
        self.bot_id = None

    # ==========================================================
    # PERSISTENCIA
    # ==========================================================

    def _init_storage(self):

        self.db = sqlite3.connect(
            self.db_path
        )

        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_state (
                user_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )

        self.db.commit()

        for uid, payload in self.db.execute(
            "SELECT user_id, data FROM user_state"
        ):

            try:
                self.users[int(uid)] = json.loads(
                    payload
                )

            except (
                ValueError,
                TypeError,
                json.JSONDecodeError
            ):
                continue

    def save_user(self, user_id):

        payload = json.dumps(
            self.users.get(user_id, {}),
            ensure_ascii=False
        )

        self.db.execute(
            """
            INSERT INTO user_state(user_id, data)
            VALUES(?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET data=excluded.data
            """,
            (
                str(user_id),
                payload
            )
        )

        self.db.commit()

    # ==========================================================
    # VARIABLES Y RESOLUCIÓN DE VALORES
    # ==========================================================

    def resolve(self, expression: str, ctx: dict):

        expression = expression.strip()

        if not expression:
            return None

        if expression.startswith("@"):
            return self.mention(
                expression,
                ctx
            )

        if expression.startswith("math.random"):

            body, _ = _extract_braced(
                expression,
                0
            )

            values = _split_top(body)

            if len(values) != 2:
                raise TGLRuntimeError(
                    "math.random necesita dos límites: "
                    "math.random {mínimo, máximo}."
                )

            minimum = int(
                self.resolve(values[0], ctx)
            )

            maximum = int(
                self.resolve(values[1], ctx)
            )

            return random.randint(
                minimum,
                maximum
            )

        if expression.startswith("math.operation"):

            body, _ = _extract_braced(
                expression,
                0
            )

            return self.eval_math(
                body,
                ctx
            )

        if expression.startswith("user."):

            value = ctx.get(
                "user_data",
                {}
            )

            bits = expression.split(".")[1:]

        else:
            bits = expression.split(".")

            root = bits.pop(0)

            if root in self.data:
                value = self.data[root]

            elif root in self.p.constants:
                value = self.p.constants[root]

            else:
                return expression

        for bit in bits:

            if isinstance(value, dict):
                value = value.get(bit)

            elif isinstance(value, list):

                try:
                    value = value[int(bit) - 1]

                except (
                    ValueError,
                    IndexError
                ):
                    return None

            else:
                return None

        return value

    # ==========================================================
    # MENCIONES DE TELEGRAM
    # ==========================================================

    def mention(self, key: str, ctx: dict):

        user = ctx.get("user")
        chat = ctx.get("chat")
        message = ctx.get("message")

        reply = getattr(
            message,
            "reply_to_message",
            None
        )

        now = datetime.now()

        values = {

            "@user": getattr(
                user,
                "first_name",
                ""
            ),

            "@user.name": getattr(
                user,
                "first_name",
                ""
            ),

            "@user.fullname": getattr(
                user,
                "full_name",
                ""
            ),

            "@user.username": getattr(
                user,
                "username",
                ""
            ) or "",

            "@user.firstname": getattr(
                user,
                "first_name",
                ""
            ),

            "@user.lastname": getattr(
                user,
                "last_name",
                ""
            ) or "",

            "@user.id": getattr(
                user,
                "id",
                ""
            ),

            "@user.language": getattr(
                user,
                "language_code",
                ""
            ) or "",

            "@user.isbot": getattr(
                user,
                "is_bot",
                False
            ),

            "@user.mention": (
                f'<a href="tg://user?id={getattr(user, "id", 0)}">'
                f'{getattr(user, "first_name", "")}</a>'
            ),

            "@chat.id": getattr(
                chat,
                "id",
                ""
            ),

            "@chat.type": getattr(
                chat,
                "type",
                ""
            ),

            "@chat.username": getattr(
                chat,
                "username",
                ""
            ) or "",

            "@message.id": getattr(
                message,
                "message_id",
                ""
            ),

            "@message.text": getattr(
                message,
                "text",
                ""
            ) or "",

            "@message.date": str(
                getattr(message, "date", "")
            ),

            "@message.type": (
                "text"
                if getattr(message, "text", None)
                else "other"
            ),

            "@message.reply": reply is not None,

            "@message.reply.id": getattr(
                reply,
                "message_id",
                None
            ),

            "@reply.user": getattr(
                getattr(reply, "from_user", None),
                "first_name",
                ""
            ),

            "@reply.user.id": getattr(
                getattr(reply, "from_user", None),
                "id",
                ""
            ),

            "@reply.text": getattr(
                reply,
                "text",
                ""
            ) or "",

            "@time.hour": now.hour,
            "@time.minute": now.minute,
            "@time.second": now.second,

            "@date.day": now.day,
            "@date.month": now.month,
            "@date.year": now.year,

            "@date.full": now.strftime(
                "%Y-%m-%d"
            ),

            "@random.number": random.randint(
                1,
                100
            ),

            "@random.user": getattr(
                user,
                "id",
                None
            ),

            "@bot": self.bot_username,
            "@bot.name": self.bot_username,
            "@bot.username": self.bot_username,
            "@bot.id": self.bot_id
        }

        return values.get(
            key,
            ""
        )

    def interpolate(self, text, ctx):

        if not isinstance(text, str):
            return str(text)

        text = re.sub(
            r"\{\$(.*?)\}",
            lambda match: str(
                self.resolve(
                    match.group(1),
                    ctx
                )
            ),
            text
        )

        text = re.sub(
            r"(?<![\w$])@[A-Za-z][\w.]*(?:\.[\w]+)*",
            lambda match: str(
                self.mention(
                    match.group(0),
                    ctx
                )
            ),
            text
        )

        return text

    # ==========================================================
    # OPERACIONES MATEMÁTICAS
    # ==========================================================

    def eval_math(self, expression: str, ctx: dict):

        original = expression.strip()

        target = re.match(
            r"\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*([+*/%-])",
            original
        )

        replaced = re.sub(
            r"\b(?:user\.)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+",
            lambda match: str(
                self.resolve(
                    match.group(0),
                    ctx
                )
                if self.resolve(
                    match.group(0),
                    ctx
                ) is not None
                else 0
            ),
            original
        )

        try:
            tree = ast.parse(
                replaced,
                mode="eval"
            )

            def evaluate(node):

                if isinstance(node, ast.Expression):
                    return evaluate(node.body)

                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, (int, float))
                ):
                    return node.value

                if (
                    isinstance(node, ast.BinOp)
                    and type(node.op) in _ARITHMETIC
                ):
                    return _ARITHMETIC[type(node.op)](
                        evaluate(node.left),
                        evaluate(node.right)
                    )

                if (
                    isinstance(node, ast.UnaryOp)
                    and isinstance(node.op, (ast.USub, ast.UAdd))
                ):
                    value = evaluate(node.operand)

                    return (
                        -value
                        if isinstance(node.op, ast.USub)
                        else value
                    )

                raise ValueError(
                    "Operación no permitida"
                )

            result = evaluate(tree)

        except Exception as exc:
            raise TGLRuntimeError(
                f"Operación matemática no válida: {original}"
            ) from exc

        # Si la operación comienza por una propiedad,
        # se actualiza esa propiedad.
        if target:

            path = target.group(1).split(".")

            root = path[0]

            prop = (
                path[1]
                if len(path) > 1
                else None
            )

            if prop and root in self.p.constants:

                obj = self.p.constants[root]

                if (
                    result < 0
                    and not obj.get("negativeOn", False)
                ):
                    return obj.get(
                        prop,
                        0
                    )

                obj[prop] = result

            elif root == "user" and prop:

                data = ctx.setdefault(
                    "user_data",
                    {}
                )

                if (
                    result < 0
                    and not data.get("negativeOn", False)
                ):
                    return data.get(
                        prop,
                        0
                    )

                data[prop] = result

        return result

    # ==========================================================
    # CONDICIONES
    # ==========================================================

    def _operand(self, raw: str, ctx: dict):

        raw = raw.strip()

        literal = _literal(raw)

        if literal is not raw and not (
            isinstance(literal, str)
            and literal == raw
        ):
            return literal

        if raw.startswith(("'", '"')):
            return literal

        return self.resolve(
            raw,
            ctx
        )

    def condition(self, raw: str, ctx: dict) -> bool:

        text = raw.strip()

        if (
            text.startswith("(")
            and text.endswith(")")
        ):
            return self.condition(
                text[1:-1],
                ctx
            )

        # OR
        pieces = _split_boolean(
            text,
            "or"
        )

        if len(pieces) > 1:
            return any(
                self.condition(piece, ctx)
                for piece in pieces
            )

        # AND
        pieces = _split_boolean(
            text,
            "and"
        )

        if len(pieces) > 1:
            return all(
                self.condition(piece, ctx)
                for piece in pieces
            )

        # NOT
        if text.lower().startswith("not "):
            return not self.condition(
                text[4:],
                ctx
            )

        # Comprobación de pertenencia a una lista.
        member = re.fullmatch(
            r"user\s+is\s+([\w]+)",
            text
        )

        if member:

            values = self.data.get(
                member.group(1),
                []
            )

            user_id = getattr(
                ctx.get("user"),
                "id",
                None
            )

            if not isinstance(values, list):
                return False

            return any(
                isinstance(item, dict)
                and str(item.get("id")) == str(user_id)
                for item in values
            )

        # Admite:
        # dinero.value >= 10
        # dinero.value is >= 10

        text = re.sub(
            r"\bis\s+(?=(?:>=|<=|!=|==|=|>|<))",
            "",
            text,
            count=1
        )

        match = re.fullmatch(
            r"(.+?)\s*(>=|<=|!=|==|=|>|<)\s*(.+)",
            text
        )

        if match:

            left = self._operand(
                match.group(1),
                ctx
            )

            right = self._operand(
                match.group(3),
                ctx
            )

            try:
                return bool(
                    _COMPARE[match.group(2)](
                        left,
                        right
                    )
                )

            except TypeError:

                try:
                    return bool(
                        _COMPARE[match.group(2)](
                            float(left),
                            float(right)
                        )
                    )

                except (
                    TypeError,
                    ValueError
                ):
                    return False

        return bool(
            self._operand(
                text,
                ctx
            )
        )

    # ==========================================================
    # ANÁLISIS DE ACCIONES
    # ==========================================================

    def _parse_action(self, raw: str):

        text = raw.strip()

        if text.startswith("return "):
            text = text[7:].strip()

        match = re.match(
            r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*",
            text
        )

        if not match:
            raise TGLRuntimeError(
                f"Acción no reconocida: {text[:100]}"
            )

        qualified = match.group(1)

        rest = text[match.end():].strip()

        body = ""

        if rest.startswith("{"):

            body, end = _extract_braced(
                rest,
                0
            )

            if rest[end:].strip():
                raise TGLRuntimeError(
                    f"Texto inesperado después de la acción "
                    f"'{qualified}': {rest[end:].strip()}"
                )

        if qualified.startswith("math."):

            name = qualified
            target = None

        elif "." in qualified:

            name, target = qualified.split(
                ".",
                1
            )

        else:
            name = qualified
            target = None

        return name, target, body

    def _parse_if(self, text: str):
        """
        Reconoce:

        if condición return acción {...} else return acción {...}

        También reconoce:

        if condición {
            acciones
        } else {
            acciones
        }
        """

        source = text.strip()

        if not source.startswith("if "):
            return None

        depth = 0
        quote = None
        escaped = False
        return_at = -1

        index = 3

        while index < len(source):

            char = source[index]

            if quote:

                if char == quote and not escaped:
                    quote = None

                if char == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False

            elif char in ("'", '"'):
                quote = char

            elif char in "{[(":
                depth += 1

            elif char in "}])":
                depth = max(
                    0,
                    depth - 1
                )

            elif (
                depth == 0
                and source.startswith("return ", index)
            ):
                return_at = index
                break

            index += 1

        # Forma con return.
        if return_at >= 0:

            condition = source[3:return_at].strip()

            yes_text = source[
                return_at + len("return "):
            ].strip()

            depth = 0
            quote = None
            escaped = False
            else_at = -1

            index = 0

            while index < len(yes_text):

                char = yes_text[index]

                if quote:

                    if char == quote and not escaped:
                        quote = None

                    if char == "\\" and not escaped:
                        escaped = True
                    else:
                        escaped = False

                elif char in ("'", '"'):
                    quote = char

                elif char in "{[(":
                    depth += 1

                elif char in "}])":
                    depth = max(
                        0,
                        depth - 1
                    )

                elif (
                    depth == 0
                    and yes_text.startswith("else", index)
                    and (
                        index == 0
                        or yes_text[index - 1].isspace()
                    )
                ):
                    else_at = index
                    break

                index += 1

            if else_at >= 0:

                yes = yes_text[:else_at].strip()

                no = yes_text[
                    else_at + 4:
                ].strip().removeprefix("return ").strip()

                return condition, yes, no

            return condition, yes_text, ""

        # Forma con bloques.
        opening = source.find("{")

        if opening < 0:
            return None

        condition = source[3:opening].strip()

        yes, after = _extract_braced(
            source,
            opening
        )

        rest = source[after:].strip()

        no = ""

        if rest.startswith("else"):

            no, _ = _extract_braced(
                rest,
                rest.find("{")
            )

        return condition, yes, no

    # ==========================================================
    # EJECUTOR DE ACCIONES
    # ==========================================================

    async def execute_actions(self, raw: str, ctx: dict):

        source = (raw or "").strip()

        if not source:
            return

        # WHILE
        if source.startswith("while "):

            opening = source.find("{")

            if opening < 0:
                raise TGLRuntimeError(
                    "while necesita un bloque: "
                    "while condición { acciones }"
                )

            condition_text = source[
                6:opening
            ].strip()

            loop_body, _ = _extract_braced(
                source,
                opening
            )

            iterations = 0

            while self.condition(
                condition_text,
                ctx
            ):

                iterations += 1

                if iterations > 10000:
                    raise TGLRuntimeError(
                        "El bucle while superó 10000 iteraciones; "
                        "se detuvo para evitar bloquear el bot."
                    )

                await self.execute_actions(
                    loop_body,
                    ctx
                )

                if iterations % 100 == 0:
                    await asyncio.sleep(0)

            return

        # IF / ELSE
        conditional = self._parse_if(
            source
        )

        if conditional:

            condition, yes, no = conditional

            await self.execute_actions(
                yes if self.condition(condition, ctx) else no,
                ctx
            )

            return

        # Secuencia de acciones.
        for part in _split_top(source):

            if part.startswith("if "):
                await self.execute_actions(
                    part,
                    ctx
                )
                continue

            name, target, body = self._parse_action(
                part
            )

            # RESET
            if name in (
                "reset",
                "resetmessage"
            ):

                self.pending.pop(
                    (
                        ctx["chat"].id,
                        ctx["user"].id
                    ),
                    None
                )

            # TEMPORIZADORES
            elif name == "start":

                timer_name = (
                    body
                    or target
                    or ""
                ).strip().strip("{} ").split(".")[0]

                timer = self.p.constants.get(
                    timer_name,
                    {}
                )

                duration = timer.get(
                    "ontime"
                )

                if duration is None:
                    raise TGLRuntimeError(
                        f"No existe un temporizador ontime llamado '{timer_name}'."
                    )

                matching = [
                    rule
                    for rule in self.p.rules
                    if (
                        rule.get("kind") == "if"
                        and re.search(
                            rf"\b{re.escape(timer_name)}\b",
                            rule.get(
                                "condition",
                                rule.get("raw", "")
                            )
                        )
                    )
                ]

                for rule in matching:

                    actions = rule.get(
                        "body",
                        ""
                    )

                    async def finish(
                        wait=duration,
                        action_text=actions,
                        saved_ctx=ctx.copy()
                    ):

                        await asyncio.sleep(
                            self.duration(wait)
                        )

                        await self.execute_actions(
                            action_text,
                            saved_ctx
                        )

                        if saved_ctx.get("user"):
                            self.save_user(
                                saved_ctx["user"].id
                            )

                    task = asyncio.create_task(
                        finish()
                    )

                    self.tasks.add(task)

                    task.add_done_callback(
                        self.tasks.discard
                    )

            # ELIMINAR MENSAJES
            elif name == "deletemessage":

                message_id = self.message_ids.get(
                    (
                        ctx["chat"].id,
                        str(target)
                    )
                )

                if message_id:

                    try:
                        await self.app.bot.delete_message(
                            ctx["chat"].id,
                            message_id
                        )

                    except Exception:
                        pass

            # EDITAR MENSAJES
            elif name == "editmessage":

                await self.send_payload(
                    ctx["chat"].id,
                    body,
                    ctx,
                    edit_id=target
                )

            # ENVIAR MENSAJES
            elif name == "sendmessage":

                await self.send_payload(
                    ctx["chat"].id,
                    body,
                    ctx
                )

            # MATEMÁTICAS
            elif name == "math.operation":

                self.eval_math(
                    body,
                    ctx
                )

                self.save_user(
                    ctx["user"].id
                )

            # URL
            elif name == "url":
                continue

            # CONSTANTES TEMPORIZADOR
            elif (
                name in self.p.constants
                and "ontime" in self.p.constants[name]
            ):

                await asyncio.sleep(
                    self.duration(
                        self.p.constants[name]["ontime"]
                    )
                )

            else:
                raise TGLRuntimeError(
                    f"Acción desconocida: {name}"
                )

    @staticmethod
    def duration(value):

        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*",
            str(value)
        )

        if not match:

            try:
                return float(value)

            except (
                TypeError,
                ValueError
            ) as exc:
                raise TGLRuntimeError(
                    f"Duración no válida: {value}"
                ) from exc

        amount = float(
            match.group(1)
        )

        unit = match.group(2)

        return amount * {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400
        }[unit]

    # ==========================================================
    # CAMPOS DE MENSAJES
    # ==========================================================

    def _payload_fields(self, body):

        from .parser import (
            _fields,
            _value
        )

        return [
            (
                field,
                [
                    _value(value)
                    for value in groups
                ],
                groups
            )
            for field, groups in _fields(body)
        ]

    # ==========================================================
    # BOTONES TELEGRAM
    # ==========================================================

    def _button_markup(
        self,
        buttons,
        chat_id,
        user_id
    ):

        from telegram import (
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            KeyboardButton,
            ReplyKeyboardMarkup
        )

        if not buttons:
            return None

        rows = []
        keyboard_rows = []

        use_keyboard = any(
            str(button.get("type", "inline")).lower() == "keyboard"
            for button in buttons
        )

        for button in buttons:

            label = str(
                button.get("text", "")
            )

            action = ", ".join(
                button.get("actions", [])
            )

            if str(
                button.get("type", "inline")
            ).lower() == "keyboard":

                keyboard_rows.append([
                    KeyboardButton(label)
                ])

                self.keyboard_actions[
                    (
                        chat_id,
                        user_id,
                        label
                    )
                ] = action

            else:

                token = uuid.uuid4().hex[:20]

                self.callback_actions[token] = action

                rows.append([
                    InlineKeyboardButton(
                        label,
                        callback_data="tgl:" + token
                    )
                ])

        if use_keyboard:

            return ReplyKeyboardMarkup(
                keyboard_rows,
                resize_keyboard=True
            )

        return InlineKeyboardMarkup(rows)

    # ==========================================================
    # ENVIAR / EDITAR MENSAJES
    # ==========================================================

    async def send_payload(
        self,
        chat_id,
        body,
        ctx,
        edit_id=None
    ):

        text = ""
        buttons = []
        message_key = None

        for field, values, groups in self._payload_fields(body):

            if field == "txt":

                text = self.interpolate(
                    str(
                        values[0]
                        if values
                        else ""
                    ),
                    ctx
                )

                message_key = (
                    values[1]
                    if len(values) > 1
                    else None
                )

            elif field == "btn":

                buttons.append({
                    "text": values[0] if values else "",
                    "id": values[1] if len(values) > 1 else None,
                    "type": values[2] if len(values) > 2 else "inline",
                    "actions": groups[3:]
                })

        markup = self._button_markup(
            buttons,
            chat_id,
            ctx["user"].id
        )

        if edit_id:

            message_id = self.message_ids.get(
                (
                    chat_id,
                    str(edit_id)
                )
            )

            if message_id:

                sent = await self.app.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                    parse_mode="HTML"
                )

            else:

                sent = await self.app.bot.send_message(
                    chat_id,
                    text,
                    reply_markup=markup,
                    parse_mode="HTML"
                )

        else:

            sent = await self.app.bot.send_message(
                chat_id,
                text,
                reply_markup=markup,
                parse_mode="HTML"
            )

        if message_key:

            self.message_ids[
                (
                    chat_id,
                    str(message_key)
                )
            ] = sent.message_id

    # ==========================================================
    # EJECUTAR COMANDOS
    # ==========================================================

    async def run_command(self, update, context):

        message = update.effective_message

        command_name = (
            message.text
            .split()[0]
            .lstrip("/")
            .split("@")[0]
        )

        command = self.p.commands.get(
            command_name
        )

        if not command:

            await message.reply_text(
                "Ese comando no existe."
            )

            return

        user = update.effective_user
        chat = update.effective_chat

        user_data = self.users.setdefault(
            user.id,
            {}
        )

        ctx = {
            "user": user,
            "chat": chat,
            "message": message,
            "user_data": user_data
        }

        # Protección de comandos mediante listas.
        guard = re.search(
            r"\bif\s+user\s+is\s+([A-Za-z_]\w*)",
            command.get("raw", "")
        )

        if guard and not self.condition(
            "user is " + guard.group(1),
            ctx
        ):

            await message.reply_text(
                "No tienes acceso a este comando."
            )

            return

        buttons = command.get(
            "btn",
            []
        )

        markup = self._button_markup(
            buttons,
            chat.id,
            user.id
        )

        # TEXTOS
        for index, item in enumerate(
            command.get("txt", [])
        ):

            content = self.interpolate(
                str(item.get("text", "")),
                ctx
            )

            sent = await message.reply_text(
                content,
                parse_mode="HTML",
                reply_markup=(
                    markup
                    if index == len(command.get("txt", [])) - 1
                    else None
                )
            )

            if item.get("id"):

                self.message_ids[
                    (
                        chat.id,
                        str(item["id"])
                    )
                ] = sent.message_id

        # ARCHIVOS
        for item in command.get("file", []):

            file_value = self.interpolate(
                str(item.get("path", "")),
                ctx
            )

            try:

                if re.match(
                    r"^https?://",
                    file_value,
                    re.I
                ):

                    sent = await self.app.bot.send_document(
                        chat.id,
                        file_value
                    )

                else:

                    path = (
                        self.base / file_value
                    ).resolve()

                    if not path.is_file():
                        raise FileNotFoundError(path)

                    with path.open("rb") as stream:

                        sent = await self.app.bot.send_document(
                            chat.id,
                            stream
                        )

                if item.get("id"):

                    self.message_ids[
                        (
                            chat.id,
                            str(item["id"])
                        )
                    ] = sent.message_id

            except Exception as exc:

                await message.reply_text(
                    f"No se pudo enviar el archivo: {exc}"
                )

        # RESPUESTAS
        for item in command.get("answer", []):

            self.pending[
                (
                    chat.id,
                    user.id
                )
            ] = {
                "expected": self.interpolate(
                    str(item.get("text", "")),
                    ctx
                ),
                "actions": item.get(
                    "actions",
                    []
                )
            }

        self.save_user(
            user.id
        )

    # ==========================================================
    # RESPUESTAS DE TEXTO
    # ==========================================================

    async def on_text(self, update, context):

        message = update.effective_message

        user = update.effective_user
        chat = update.effective_chat

        key = (
            chat.id,
            user.id
        )

        # Primero comprobar botones de teclado normal.
        keyboard_action = self.keyboard_actions.get(
            (
                chat.id,
                user.id,
                (message.text or "").strip()
            )
        )

        if keyboard_action is not None:

            ctx = {
                "user": user,
                "chat": chat,
                "message": message,
                "user_data": self.users.setdefault(
                    user.id,
                    {}
                )
            }

            await self.execute_actions(
                keyboard_action,
                ctx
            )

            self.save_user(
                user.id
            )

            return

        pending = self.pending.get(
            key
        )

        if not pending:
            return

        if (
            (message.text or "").strip().casefold()
            != str(pending["expected"]).casefold()
        ):
            return

        self.pending.pop(
            key,
            None
        )

        ctx = {
            "user": user,
            "chat": chat,
            "message": message,
            "user_data": self.users.setdefault(
                user.id,
                {}
            )
        }

        await self.execute_actions(
            ", ".join(pending["actions"]),
            ctx
        )

        self.save_user(
            user.id
        )

    # ==========================================================
    # CALLBACKS DE BOTONES INLINE
    # ==========================================================

    async def on_callback(self, update, context):

        query = update.callback_query

        await query.answer()

        token = query.data.removeprefix(
            "tgl:"
        )

        action = self.callback_actions.get(
            token
        )

        if action is None:
            return

        user = query.from_user
        chat = query.message.chat

        ctx = {
            "user": user,
            "chat": chat,
            "message": query.message,
            "user_data": self.users.setdefault(
                user.id,
                {}
            )
        }

        await self.execute_actions(
            action,
            ctx
        )

        self.save_user(
            user.id
        )

    # ==========================================================
    # EVENTOS DE MIEMBROS
    # ==========================================================

    async def on_member(self, update, context):

        change = update.chat_member

        old_status = change.old_chat_member.status
        new_status = change.new_chat_member.status

        joined = (
            new_status in (
                "member",
                "administrator"
            )
            and old_status in (
                "left",
                "kicked"
            )
        )

        left = (
            old_status in (
                "member",
                "administrator"
            )
            and new_status in (
                "left",
                "kicked"
            )
        )

        event_name = (
            "join"
            if joined
            else "leave"
            if left
            else None
        )

        if not event_name:
            return

        user = change.new_chat_member.user

        ctx = {
            "user": user,
            "chat": change.chat,
            "message": None,
            "user_data": self.users.setdefault(
                user.id,
                {}
            )
        }

        for event in self.p.events:

            if event.get("event") == event_name:

                await self.execute_actions(
                    event.get("body", ""),
                    ctx
                )

        self.save_user(
            user.id
        )

    # ==========================================================
    # CONSTRUIR APLICACIÓN TELEGRAM
    # ==========================================================

    def build(self):

        try:

            from telegram import BotCommand

            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                ChatMemberHandler,
                CommandHandler,
                MessageHandler,
                filters
            )

        except ImportError as exc:

            raise TGLRuntimeError(
                "Falta python-telegram-bot. "
                "Instala las dependencias con: "
                "python -m pip install -r requirements.txt"
            ) from exc

        if not self.token:

            raise TGLRuntimeError(
                "Falta var BOT_TOKEN (o TELEGRAM_BOT)."
            )

        app = (
            Application
            .builder()
            .token(str(self.token))
            .build()
        )

        self.app = app

        for command_name in self.p.commands:

            app.add_handler(
                CommandHandler(
                    command_name,
                    self.run_command
                )
            )

        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.on_text
            )
        )

        app.add_handler(
            CallbackQueryHandler(
                self.on_callback
            )
        )

        app.add_handler(
            ChatMemberHandler(
                self.on_member,
                ChatMemberHandler.CHAT_MEMBER
            )
        )

        async def startup(application):

            me = await application.bot.get_me()

            self.bot_username = (
                "@" + (me.username or "")
            )

            self.bot_id = me.id

            print(
                f"[TGL] Conectado como {self.bot_username} "
                f"(ID {self.bot_id})"
            )

            print(
                f"[TGL] Comandos cargados: {len(self.p.commands)}"
            )

            if self.p.cmdlist:

                await application.bot.set_my_commands([
                    BotCommand(
                        name,
                        str(description)[:256]
                    )
                    for name, description in self.p.cmdlist.items()
                ])

        app.post_init = startup

        return app

    # ==========================================================
    # INICIAR BOT
    # ==========================================================

    def run(self):

        app = self.build()

        print(
            f"[TGL] Iniciando {self.path.name}"
        )

        app.run_polling(
            allowed_updates=[
                "message",
                "callback_query",
                "chat_member"
            ]
        )