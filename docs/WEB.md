# Web privada de Miku: primer corte

Estado: adaptador e interfaz implementados para integración privada. No hay un
comando de acceso remoto ni un autenticador de producción activado. Crear la app
sin verificador de identidad y propietario deniega todas las rutas. Flask 3.1.3 y
Waitress 3.0.2 se instalaron con autorización y se verificaron con datos sintéticos.

## Alcance

La interfaz permite listar, crear y consultar conversaciones persistentes del
perfil fijo `miku`, continuar las abiertas, enviar texto y cerrar explícitamente.
Las cerradas siguen consultables; no se reabren desde la web. Quedan excluidos
otros perfiles, sesiones `action`, sesiones temporales y ramas. El listado toma
las últimas 200 sesiones del repositorio y filtra las elegibles; no hay paginación.
El detalle carga el historial completo de la sesión.

El navegador es una interfaz del runtime Python. `SessionService` conserva la
semántica de sesiones y `MikuTurnService.handle()` procesa cada turno. La procedencia
`web` no concede autoridad humana para acciones. No se incluyen streaming, voz,
Pet, lectura de archivos, `/allow`, Archive, edición, shell ni selección remota
de perfil, modelo, endpoint o paths. Escribir `/allow ...` sólo envía texto.

`miku.web.runtime.create_coordinator()` compone Miku con `memory_enabled=False`,
`ContextManager(include_memory=False)` y sin servicio ni recuperador de memoria.
No abre `memory.db` ni realiza extracción o adquisición. Conserva las políticas
guardadas de las sesiones para otras interfaces y no cambia identidad, prompt ni
configuración local. El historial conversacional sí entra en el contexto habitual;
desactivar memoria personal no elimina mensajes anteriores.

## Composición y dependencias

- `miku.web.app.create_app(coordinator, expected_origin=..., owner=..., authenticate=...)`
  construye Flask sin abrir sockets. `authenticate(request)` debe devolver una
  identidad verificada por una frontera de servidor confiable; sólo `owner` accede.
  Ausencia, identidad distinta o excepción del verificador producen denegación.
- `miku.web.runtime.create_coordinator(...)` exige repositorio, proveedor y ruta
  de recibos explícitos. No elige bases de desarrollo automáticamente.
- `miku.web.runtime.serve_loopback(app, port=8765)` usa Waitress sobre `127.0.0.1`,
  cuatro threads HTTP y límites de cuerpo/cabeceras. Al salir espera al turno y
  libera locks mediante `shutdown()`. No cierra conversaciones por terminar el
  servidor. Quien use otra forma de servir debe llamar a `coordinator.shutdown()`
  en `finally`.

El cierre espera una señal explícita del worker, incluso ante Ctrl+C repetido;
no deduce su terminación de `Thread.is_alive()` después de un `join()` interrumpido.
El worker verifica una autorización de arranque antes de tocar repositorio o
provider. Si el arranque falla, se cancela esa autorización: un hilo que llegue
tarde no puede ejecutar el turno. El recibo queda fallido y se requiere reiniciar
antes de aceptar otros envíos.

El extra opcional `web` declara Flask `>=3.1.3,<4` y Waitress `>=3.0.2,<4`.
La CLI normal no los importa. No se agregan dependencias JavaScript. Flask usa
Werkzeug, Jinja2, itsdangerous, Click y Blinker, más sus dependencias transitivas.
La instalación fue autorizada para este entorno, preservando las versiones de las
20 distribuciones previas. Este documento no activa servicios. Waitress funciona
en Windows sin un servidor de desarrollo.

## Frontera de seguridad

Todas las rutas, assets, lecturas de historial y consultas de recibos exigen la
misma autenticación. La conexión al backend debe ser loopback. Host y origen se
comparan con el origen explícito de configuración; no se deducen de cabeceras
reenviadas. No se confía por defecto en `Tailscale-*`, `Forwarded`,
`X-Forwarded-*` ni una cabecera de autorización inventada por el cliente.

Cada mutación exige `Origin` exacto, JSON, campos cerrados y `X-CSRF-Token`.
El token es aleatorio y efímero por proceso, entregado por bootstrap autenticado;
no autentica por sí solo ni se guarda como credencial persistente. Las lecturas
rechazan un `Origin` presente ajeno. No hay CORS. Las respuestas llevan `no-store`,
CSP, `nosniff`, protección de frames y política de referencia restrictiva.

La interfaz renderiza contenido con `textContent`, sin ejecutar HTML o Markdown.
Errores HTTP inesperados no serializan excepciones. Los mensajes etiquetados
`provider_error` se presentan con texto genérico, pero el historial existente en
SQLite no se reescribe: el contexto posterior aún puede contener el diagnóstico
original y un modelo podría mencionarlo. No es un mecanismo de depuración o
redacción integral de conversaciones para compartir con otras personas.

Todavía falta integrar y verificar un autenticador real de Tailscale Serve,
identidad exacta del propietario y origen HTTPS. Una función que siempre devuelve
el propietario sólo es una fixture de pruebas sintéticas, nunca autenticación de
producción. No hay bypass activable desde HTTP ni modo de login compartido.
Esta versión no ofrece separación entre varios usuarios.

## Envíos, reconexión y cierre

Se admite un único turno en curso en toda la instancia web. Un envío nuevo recibe
`409 busy` durante otro turno; no queda en cola. Crear o cerrar sesiones también
espera a que no haya un turno activo. Leer historial o consultar recibos sí es posible.

Cada envío lleva UUID propio. Antes de ejecutar se persiste un recibo separado
con UUID, sesión, hash del texto y estado. Repetir UUID y contenido devuelve ese
recibo; reutilizarlo con otro contenido o sesión falla. El resultado referencia
mensajes del repositorio, sin copiar su texto al almacén de recibos.

El worker pertenece al servidor: desconectar el teléfono o cerrar la pestaña no
cancela el turno ni cierra la conversación. La página guarda sólo UUID de envío y
sesión en `sessionStorage`, consulta el mismo recibo y recarga el historial.
No guarda prompts, respuestas ni credenciales en ese storage. La memoria de la
página puede conservar el borrador hasta confirmar el envío.

Los estados son `running`, `completed`, `failed` e `interrupted`. Tras un crash,
el nuevo propietario del almacenamiento marca recibos pendientes como
`interrupted`; nunca repite automáticamente la inferencia. Puede existir un
mensaje o respuesta ya persistido aunque el recibo no haya terminado: se debe
revisar el historial. Un fallo incierto al escribir recibos bloquea nuevos turnos
hasta reiniciar la instancia. No se promete ejecución exactamente una vez frente
a pérdida del almacenamiento; se evita replay mientras se conserva el ledger.

Dos instancias web no pueden usar simultáneamente la misma base conversacional
o de recibos gracias a locks del sistema operativo. La CLI no participa en esos
locks: no usar CLI/Pet/voz concurrentemente sobre las mismas conversaciones.
No borrar o reemplazar recibos para intentar recuperar un turno. Al restaurar o
mover datos se necesita un procedimiento explícito que conserve la relación entre
recibos e historial; no se agregó una migración o herramienta de restauración.

## Contrato HTTP

| Operación | Ruta | Datos |
| --- | --- | --- |
| Bootstrap | `GET /api/bootstrap` | Token CSRF y recibo activo, si existe |
| Listado | `GET /api/sessions` | `{sessions: [...]}` |
| Crear | `POST /api/sessions` | `{}` |
| Historial | `GET /api/sessions/<uuid>` | `{session, messages}` |
| Enviar | `POST /api/sessions/<uuid>/turns` | `{request_id, text}`; hasta 8192 caracteres |
| Recuperar | `GET /api/requests/<uuid>` | Recibo y resultado disponible |
| Cerrar | `POST /api/sessions/<uuid>/close` | `{}` |

Los errores usan `{error: {code, message}}`. Los POST necesitan las protecciones
anteriores. La web acepta cuerpos de hasta 64 KiB. Los límites no son un sistema
de cuotas, multiusuario o trabajos distribuidos.

## Verificación y siguiente checkpoint

Las pruebas del coordinador y runtime usan SQLite, perfiles y providers sintéticos,
incluidos crashes reales de procesos de prueba. Las pruebas HTTP usan el cliente
de Flask; se omiten explícitamente si el extra no está instalado. Los tests de
interfaz se ejecutan con `node --test tests/web_ui.test.cjs` sin paquetes npm.

Antes de usar conversaciones reales: acordar e implementar la identidad de Serve,
comprobar denegación real y
acceso del propietario, y habilitar explícitamente la conexión desde otro dispositivo.
Instalar/iniciar Tailscale, login, certificados, firewall, autostart y publicación
no son efectos de este corte. La PC y el runtime deben estar encendidos; una página
no los arranca a distancia.

Fuentes técnicas: [Flask y Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/),
[parámetros de Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html),
[identidad de Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).
