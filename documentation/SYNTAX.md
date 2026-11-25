# Referencia de sintaxis — Reidi TGL 0.3.1

## Comentarios

Los comentarios empiezan con `//` y terminan con `\\`:

```tgl
// Este es un comentario \\
```

## Variables, configuración e importaciones

```tgl
var BOT_TOKEN = "TOKEN";
var BOT_NAME = "Mi bot";
var {import variables.txt}
import ./database.js
```

Las rutas importadas son relativas al archivo `.tgl`. Los archivos de configuración aceptan una asignación `CLAVE=VALOR` por línea. Los archivos `.js` se leen como datos declarativos; no se ejecuta JavaScript arbitrario.

## Constantes y propiedades

```tgl
const timeout = {ontime = 10s}
const dinero = {value = 10}
const puntos = {value = 10; negativeOn}
```

`negativeOn` permite que esa propiedad admita resultados negativos. Sin esa bandera, una operación que deje el valor por debajo de cero se rechaza.

## Lista de comandos de Telegram

```tgl
cmdlist{
    "start":"Inicia el bot",
    "reg":"Registro en el bot"
};
```

## Comandos y mensajes

```tgl
cmd start = {
    txt = {"Hola @user"},{welcome};
}
```

`txt` recibe texto y un identificador opcional. Los identificadores son por chat.

## Botones

```tgl
cmd menu = {
    txt = {"Elige una opción"},{menu};
    btn = {"Abrir"},{null},{inline},{sendmessage {txt = {"Abierto"}}};
}
```

Tipos: `inline` y `keyboard`. El callback de un botón inline ejecuta sus acciones. Los botones deben acompañar un mensaje.

## Respuestas de texto

```tgl
cmd confirmar = {
    txt = {"Escribe ACEPTAR"};
    answer = {"ACEPTAR"},{null},{sendmessage {txt = {"Confirmado"}}};
}
```

La respuesta se compara sin distinguir mayúsculas/minúsculas y se asocia al chat y usuario que inició la espera.

## Archivos

```tgl
cmd documento = {
    txt = {"Aquí tienes el archivo"};
    file = {"./documentos/guia.pdf"},{guia};
}
```

También se admiten URLs HTTP/HTTPS como origen del documento.

## Datos JavaScript serializables

```tgl
js {
    const database = [
        {"numero":"1", "nombre":"Timbiriche", "edad":"25"}
    ];
}
```

Interpolación: `{$database.nombre.1}`. Los índices de arrays en TGL comienzan en 1.

## Condiciones, operadores y matemáticas

Operadores: `=`, `==`, `!=`, `<`, `>`, `<=`, `>=`.

```tgl
const dinero = {value = 10}
cmd saldo = {
    txt = {"Saldo: {$dinero.value}"};
}
```

Operaciones admitidas en `math.operation`: suma, resta, multiplicación, división, módulo y potencia.

```tgl
math.operation {dinero.value - 3}
```

Aleatorio: `math.random {1, 100}`.

La comprobación `user is admins` busca el ID del usuario dentro de un array de objetos que contengan la propiedad `id`.

## Temporizadores

```tgl
const timeout = {ontime = 10s}
cmd temporizador = {
    txt = {"Escribe iniciar"},{timer};
    answer = {"iniciar"},{null},{start {timeout}};
}
if timeout = 0 return {editmessage.timer{txt = {"Tiempo terminado"}}}
```

Unidades: `s`, `m`, `h`, `d`.

## Acciones de mensajes

- `sendmessage {txt = {"texto"}}`
- `editmessage.identificador {txt = {"nuevo texto"}}`
- `deletemessage.identificador`
- `reset` / `resetmessage` cancela la espera de respuesta pendiente de ese usuario en ese chat.

## Eventos de grupo

```tgl
on @user join {
    sendmessage {txt = {"@user se ha unido"}}
}

on @user leave {
    sendmessage {txt = {"@user ha abandonado la partida"}}
}
```

El bot necesita los permisos adecuados y recibir actualizaciones de miembros del grupo.

## Menciones

`@user`, `@user.id`, `@user.name`, `@user.fullname`, `@user.username`, `@user.firstname`, `@user.lastname`, `@user.language`, `@user.isbot`, `@user.mention`, `@bot`, `@bot.name`, `@bot.username`, `@bot.id`, `@chat.id`, `@chat.type`, `@chat.username`, `@message.id`, `@message.text`, `@message.date`, `@message.reply`, `@message.reply.id`, `@message.type`, `@reply.user`, `@reply.user.id`, `@reply.text`, `@time.hour`, `@time.minute`, `@time.second`, `@date.day`, `@date.month`, `@date.year`, `@date.full`, `@random.number`, `@random.user`.

## Idiomas

```tgl
lang = {en, ja, fr, it};
```

En esta versión se almacenan los códigos declarados; la traducción automática no está implementada todavía.
