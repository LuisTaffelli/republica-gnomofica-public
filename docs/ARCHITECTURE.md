# Arquitectura de la República Gnomófica

## Propósito

La República Gnomófica es un sistema operativo de contexto local-first.

No busca una IA mágica. Coordina humanos, habitantes, herramientas, modelos y servicios bajo responsabilidades y permisos claros.

> Miku y los habitantes interpretan y coordinan. Las capacidades producen efectos. El humano conserva la autoridad final.

---

## Estado arquitectónico

Este documento distingue tres planos para no confundir intención con realidad:

- **vigente:** contratos conceptuales que deben respetarse;
- **próxima evolución:** cambios ya priorizados, todavía no implementados;
- **futuro:** dirección compatible, no compromiso de implementación actual.

El runtime contiene una CLI común, conversación con Miku, GNOMO-ARCHIVE y
GNOMO-LABORAL. Miku coordina contexto, un provider sustituible, memoria personal
gobernada y capacidades read-only de Laboral dentro de scopes explícitos. También
existe el carril durable de intención, autoridad y auditoría previo a efectos y
una primera capability productiva de reemplazo textual seguro con recuperación
asistida. Audit posee además una consulta humana read-only que hace visibles
estados desconocidos o inconsistentes sin conceder autoridad. Miku también
coordina consultas read-only explícitas a Archive; todavía no coordina escritura
general ni un sistema general de permisos.
La presencia visual opcional de Miku observa un contrato mínimo de actividad y
no participa de cognición, memoria, autoridad ni ejecución.

---

## Entidades y responsabilidades

### Humano

Autoridad final. Define objetivos, delega capacidades y ratifica cambios canónicos importantes.

### Miku

Identidad persistente e interfaz coherente de la República.

Interpreta intención, pide contexto, decide y coordina dentro de permisos, y comunica resultados.

Miku puede decidir y coordinar acciones dentro de permisos explícitos; los efectos reales los producen herramientas o servicios de la República.

### Habitantes

Representan identidades y/o responsabilidades persistentes. No se definen por ser paquete, proceso o nodo; esa topología puede evolucionar.

- GNOMO-ARCHIVE preserva conocimiento compartido.
- GNOMO-LABORAL comprende problemas técnicos.
- Otros habitantes siguen siendo posibilidades futuras, no componentes actuales.

### Herramientas y servicios

Son capacidades concretas. Pueden observar, calcular, modificar o interactuar con sistemas externos. Sus resultados y efectos importantes deben ser verificables.

### Infraestructura cognitiva

Modelos, proveedores y agentes externos aportan capacidades cognitivas sustituibles. No poseen automáticamente la identidad, memoria, autoridad ni responsabilidad de quien los utiliza.

La capacidad cognitiva por defecto es configuración, no identidad. Puede ser local o remota según privacidad, calidad, costo, latencia, disponibilidad y permisos.

---

## Contexto y memoria

La responsabilidad de construir contexto pertenece a la República, no a un modelo ni a una clase concreta llamada `ContextBuilder`.

El contexto operativo es una proyección temporal y pertinente de:

1. invariantes normativos;
2. identidad;
3. memoria personal de Miku;
4. conocimiento compartido de Archive;
5. conversación, rama y resumen;
6. contexto de tarea, incluidos archivos y resultados de capacidades.

Los invariantes deben tener una representación operativa compacta siempre vigente. El resto se selecciona según la tarea. Disponible no significa inyectado.

Los proyectos y recursos utilizados por la República no necesitan residir dentro de su propio árbol de archivos. La República puede referenciar ubicaciones externas y tratarlas como contexto de tarea sin asumir propiedad, importarlas ni incorporarlas íntegramente a su contexto.

Tener acceso a una ubicación o proyecto no implica cargar todo su contenido al contexto. La inspección y selección de archivos debe ser pertinente a la tarea, de acuerdo con el principio de que disponible no significa inyectado.

Esta distinción no prescribe por sí misma un registro de proyectos, formato de configuración, sistema de permisos, indexación, recuperación aumentada, embeddings ni abstracción del filesystem.

Historial, resumen, memoria personal, Archive y auditoría son dominios distintos. La auditoría puede ser consultada como evidencia, pero no se convierte automáticamente en memoria.

### Ramas

Una rama hereda el historial hasta el mensaje ancla. Por defecto habla con la Miku actual y consulta memoria personal y Archive actuales. La reconstrucción histórica exacta, si se incorpora, será una operación explícita basada en procedencia y auditoría.

---

## Autoridad y efectos

La autoridad se evalúa por capacidad, efecto y contexto. Tener una herramienta no equivale a permiso irrestricto.

El modelo conceptual admite:

- prohibido;
- propuesta sin efecto;
- ejecución con aprobación;
- ejecución delegada dentro de límites.

Las operaciones read-only y de bajo riesgo pueden delegarse por defecto. Los efectos relevantes requieren aprobación salvo delegación explícita. La implementación inicial puede usar categorías simples, pero no debe impedir políticas más granulares en el futuro.

Toda acción importante debe producir evidencia proporcional a su riesgo y ser reversible cuando sea técnicamente posible.

---

## Acceso y distribución

Local-first no implica una única máquina ni ejecución local en cada dispositivo.

La República debe poder ser accesible desde varios dispositivos antes de distribuir por completo sus capacidades. Un equipo principal puede ejecutar modelos pesados mientras celulares u otros nodos actúan como interfaces o participantes limitados.

La ejecución distribuida completa, los jobs de larga duración y el Concilio son futuro. La arquitectura actual sólo debe evitar dependencias innecesarias de una única interfaz, proveedor o directorio de ejecución.

---

## Flujo conversacional implementado

```text
Fuentes de República + memoria personal + sesión/rama + task explícito opcional
  ↓
MemoryRetriever (selección textual acotada de recuerdos pertinentes)
  ↓
ContextManager (ensamblado sin persistencia)
  ↓
ContextProjection (invariants, identity, instructions, memory, conversation, task)
  ↓
PromptComposer.serialize (texto determinista con procedencia)
  ↓
TextProvider.ask(str) → OllamaProvider → modelo configurado
  ↓
filtro barato → MemoryExtractor → MemoryPolicy → MemoryRepository
```

`OllamaProvider` usa el campo top-level `think` de `/api/generate`, configurable
por entorno y desactivado por defecto para la conversación actual. La traza
separada `thinking` nunca forma parte del valor devuelto por `TextProvider`; el
prompt y `TurnRequest` no se modifican para controlar esa opción. Esto sigue
siendo configuración del provider, no routing ni una regla propia de Qwen.

Inspección usa la extensión operativa `StructuredTextProvider.ask_structured(prompt,
schema) -> str`. `OllamaProvider` traduce el esquema a `format` en esa petición,
sin modificar su configuración ni el contrato `ask(str)` de los demás consumidores.
La restricción de generación no sustituye validación ni concede autoridad.

La CLI ensambla e inyecta los componentes en Miku, que sólo coordina su uso.
`core/context_builder.py` conserva un alias transitorio al mismo manager,
sin una implementación paralela. Los nombres concretos describen el runtime,
no añaden contratos canónicos.

Cada recuerdo recuperado se proyecta sin mutar storage como un hecho con
`subject=user`, nombre humano tomado del perfil y procedencia. Así una frase
persistida en primera persona no queda disponible al modelo como voz ambigua de
Miku. La CLI puede insertar antes del provider un observer opt-in
`--context-debug`: renderiza estructura y procedencia acotadas, omite payloads
observados de Laboral/Archive y redacta secretos, sin serializar el prompt entero.

### Presencia visual de Miku

`core.activity` define solamente `ActivityState`, `ActivityEvent` y
`ActivitySink`, con un sink nulo por defecto. Miku y `ActionCoordinator` producen
estados verificables a través de esa frontera: provider y Archive usan
`thinking`, Laboral usa `inspecting`, una propuesta effectful espera aprobación y
la ejecución ya iniciada usa `acting`. La finalización correcta vuelve a `idle` y
una falla visible deja `error` hasta una actividad posterior. `listening` y
`speaking` pertenecen al vocabulario, pero aún no tienen productor operativo.
Todo sink se adapta en una frontera best-effort común: sus excepciones quedan
diagnosticables en el adaptador y nunca se propagan al dominio observado. En
particular, una falla visual posterior a `EXECUTION_STARTED` no puede impedir la
invocación de la capability ni su auditoría final.

La implementación productiva vive en `miku.pet`. Su controlador implementa el
sink mediante una cola local thread-safe; no recibe prompts, resultados de
Archive/Laboral, memoria, intents ni aprobaciones. Con Pet, el loop bloqueante de
consola corre en un hilo de trabajo y el main loop de Tkinter permanece en el
hilo principal. La vista drena la cola mediante `after`, por lo que ningún hilo de
trabajo llama a Tk. Cerrar la ventana no cancela abruptamente el chat o un efecto
en curso; desactiva el sink visual, vacía su cola acotada y el flujo de consola
puede terminar normalmente. Ctrl+C en main solicita parada mediante un Event
separado del sink de actividad y espera la señal de finalización del worker,
incluso ante interrupciones repetidas. Chat y voz consultan esa solicitud en
límites seguros; sólo el worker termina el turno y cierra su sesión. El sink
continúa sin conceder autoridad ni cancelar operaciones. La espera puede requerir
Enter/EOF o el retorno de provider/STT/TTS. El hilo conserva su flag daemon, pero
la salida manejada de runtime no lo abandona. Sus errores se reportan por una vía
controlada. Sin Pet se inyecta el sink nulo.

En Windows la vista intenta `overrideredirect`, topmost configurable y
`-transparentcolor` con un color-key. Si Tk rechaza la opción, degrada a una
ventana pequeña normal. Otros sistemas usan directamente esa variante portable.
El placeholder se dibuja sólo en la vista y puede sustituirse por sprites sin
cambiar el contrato ni el core.

`docs/INVARIANTS.md` es una proyección operativa derivada del canon, no otro
nivel normativo. Se carga siempre junto con identidad e instrucciones; su
ausencia o contenido vacío causa un error explícito. Los documentos declarados
en el perfil permanecen disponibles, sin inyección automática ni recuperación
semántica. La representación operativa no implementa un sistema de permisos.

La conversación conserva los objetos Message hasta serializar: últimos 20
mensajes anteriores, exclusión del turno actual duplicado y lectura del resumen
preexistente de la sesión persistente, sin activar el summarizer. SessionService
continúa preparando el camino de cada rama. Task es una colección explícita y
ordenada de contenido/procedencia; no tiene persistencia ni promoción automática.

La persistencia conversacional v2 usa `(timestamp, ordinal)` para cargar sesiones,
ventanas recientes y rangos alrededor de un ancla. El ordinal positivo es único
por sesión, se asigna en el mismo INSERT del mensaje y permanece interno al
repositorio; UUID, `Message` y referencias de ramas/resúmenes no cambian. La
migración asigna ordinales históricos según el orden visible v1 `(timestamp, id)`.
Creación, cambios de esquema y registro de versión comparten una transacción.
Una guardia de lectura bloquea v1 antes de abrir para escritura: migrar exige
`allow_migration=True`, que ningún flujo normal activa. Una inicialización fallida
no queda en caché. La migración sólo se verificó con bases sintéticas; el
checkpoint humano del 2026-10-04 eligió reiniciar vacías las bases de uso de
conversaciones, memoria y Archive, conservando perfiles y configuración.
No cambia la prioridad del timestamp ni se
resuelven retrocesos del reloj.

Archive conserva su CLI y suma una fachada compartida read-only para Miku.
`SQLiteArchiveReader` abre su persistencia propia con `mode=ro` y `query_only`;
`ArchiveReadOnlyCapabilities` sólo registra `search_notes`, `read_note` y
`list_links`. `ArchiveLimits` centraliza cantidad de resultados y vínculos,
tamaño de fragmentos/contenido y longitud de consulta. Los resultados estructurados
incluyen procedencia relocalizable, truncación, omitidos y errores explícitos.

`ArchiveQuerySession` usa sobre `TextProvider.ask(str)` un protocolo JSON específico
con `archive_request` o `final_answer`. República valida el request antes de cada
`SELECT` y exige al menos una consulta exitosa antes de aceptar respuesta final.
La sesión sólo existe cuando el usuario activa `miku chat --archive`; en otro caso
Archive no se abre ni aparece en el prompt. Sus observaciones se agregan a `task`,
nunca a `memory`, y no conceden autoridad, canon, política ni aprobación. El loop
tiene un máximo configurable de 6 pasos por defecto. No incorpora escritura,
RAG, embeddings, índices semánticos ni resúmenes persistentes.
La activación de lectura no ejecuta la inicialización legacy de Archive: una base
ausente o un schema incompatible fallan explícitamente desde la conexión read-only
sin crear archivos, tablas o migraciones. El comando propio `archive` conserva su
inicialización porque sí administra esa persistencia.
Las respuestas conversacionales derivadas de Archive conservan una marca de
fuente observada y quedan fuera de la ventana posterior del extractor de memoria.

Laboral expone un núcleo reutilizable: `list_tree`, `read_file`, `search_text` e
`inspect_python`. Sus vías directas conservan las cuatro capabilities. En el
corte operativo S2.2, el chat `--inspect` conecta únicamente `read_file` mediante
`miku.read_binding`; árbol, búsqueda y AST quedan temporalmente fuera de ese chat.
`InspectionScope` expresa un root y targets adicionales explícitos, siempre
read-only. Los paths se normalizan y resuelven en cada acceso; un archivo autoriza
sólo ese archivo y un directorio su árbol. Los recorridos no siguen enlaces y
aplican exclusiones centralizadas para volumen y contenido no textual.

`Miku._inspect` coordina el loop usando el contrato puro `miku.read_contract`.
Mediante `ask_structured` y `READ_DECISION_SCHEMA` admite `tool_request` o
`final_answer`, incluyendo una final sin lectura previa. Un provider sin soporte
explícito termina con `provider_error`, sin fallback ni otra llamada. República
valida el texto JSON completo; Laboral valida argumentos
y scope. Se reservan hasta 8 decisiones y 4 solicitudes de lectura por turno;
inválidos y repetidos consumen presupuesto. El primer fallo termina sin reparación
automática. `ReadTurnOutcome` conserva operación, target, datos, evidencia, errores
y truncación: fallo/límite con observaciones o final truncada producen `partial`;
sin observaciones, el fallo produce `error`. Una región posterior a EOF no inventa
evidencia.

Los resultados vuelven a `ContextProjection.task` como contenido observado no
confiable, sin duplicar el contenido en el excerpt proyectado. El payload completo
permanece efímero y no se promueve a memoria, Archive, identidad, instrucciones o
canon. La respuesta/diagnóstico se guarda con procedencia; esa marca se conserva
al serializar historial y se propaga a respuestas derivadas. Debug omite esos
payloads. La ventana de extracción sólo selecciona mensajes humanos elegibles;
un parcial o error de lectura no dispara el extractor.

Una vista derivada `InspectionResult` permite consultar la evidencia de la última
inspección sin ejecutar capabilities otra vez. El objeto no entra en SQLite ni
en memoria; una inspección posterior lo reemplaza y el reinicio lo pierde. Los
fragmentos que el humano solicita mostrar pueden citarse en la respuesta marcada.
La conversación sin scope y Archive mantienen sus rutas anteriores. Los límites
de lectura no constituyen un presupuesto agregado de tiempo o contexto.

La memoria personal usa `data/memory.db`, físicamente separada de conversaciones
y Archive. `MemoryExtractor` recibe sólo el mensaje, una ventana breve y recuerdos
activos acotados, y propone candidatos de las categorías `preference`,
`personal_fact` o `shared_context`. No puede persistir. `PersonalMemoryPolicy`
descarta fuentes observadas y secretos, exige confirmación para información
sensible o significativamente ambigua y permite auto-guardado claro de bajo
riesgo. Una defensa determinista mínima eleva alergias y declaraciones obvias de
diabetes a confirmación aunque el extractor las rotule como `low`; no intenta ser
un clasificador médico general. Las confirmaciones pendientes son estado efímero
del proceso.
Antes de policy, `MemoryService` descarta referencias conversacionales efímeras
acotadas y candidatos con forma de pregunta informativa. También normaliza texto
de forma conservadora y clasifica como `already_known` un candidato equivalente a
cualquier recuerdo active: no persiste, no crea pending ni provoca invitación. Un
candidato explícitamente relacionado como `corrects` o `replaces` no atraviesa
ese atajo, para conservar correcciones reales.
El reconocimiento usa listas cerradas de aprobaciones y rechazos inequívocos:
frases mixtas, negativas ambiguas o nuevas intenciones no consumen el pending.
Cada respuesta resuelve como máximo un candidato. `--no-memory` configura un
override efímero del runtime, incluso al reanudar una sesión, sin mutar su política
persistida ni confirmar pendientes durante esa invocación.

`MemoryRepository` conserva procedencia de sesión/mensaje y evolución mediante
estados `active`/`retired` y relaciones opcionales `corrects`, `replaces` o
`contextualizes`. Retirar no borra. `MemoryRetriever` selecciona primero activos;
los retirados sólo se consultan ante una necesidad histórica clara. La estrategia
actual es textual y reemplazable, sin embeddings, vector DB ni RAG. Contenido de
archivos, web, herramientas, Archive o servicios externos sigue siendo datos: no
crea memoria ni autoridad por sí solo.
La vía administrativa `memory dedupe` aplica la misma equivalencia sobre active:
elige el registro más antiguo como canonical y sólo retira duplicados posteriores.
El operador debe revisar primero el dry-run antes de aplicar sobre datos reales;
la implementación exige un modo explícito, nunca ejecuta esta limpieza al iniciar
ni borra procedencia.
Guardar una corrección `corrects`/`replaces` y retirar el recuerdo relacionado
forma una transacción local única en `memory.db`. Los fallos de retrieval,
extracción, confirmación o persistencia no invalidan la respuesta conversacional: Miku
conserva `last_memory_error` y la CLI emite un diagnóstico discreto.

`core.actions` y `core.audit` implementan una frontera separada para futuras
acciones con efectos. El modelo sólo puede proponer datos; República valida un
`ActionIntent`, calcula un fingerprint SHA-256 canónico de capability, operación,
target, parámetros y tipo de efecto, y persiste una vista sanitizada en
`data/audit.db`. Esa vista se deriva determinísticamente de los parámetros reales:
el caller sólo declara `redacted_fields`, que se suma a los nombres sensibles
comunes y se aplica recursivamente. No existe una API para proporcionar un relato
alternativo del payload ejecutado.

El flujo effectful es:

```text
ActionIntent validado
  → ACTION_PROPOSED durable
  → ActionPolicy (effectful = aprobación; forbidden = denegado)
  → aprobación humana inequívoca del ID + fingerprint exactos
  → ACTION_APPROVED
  → EXECUTION_STARTED
  → EffectCapability.execute(intent), una invocación local
  → EXECUTION_SUCCEEDED | EXECUTION_FAILED + EffectResult/evidencia
```

`ActionCoordinator` es la única vía normal de invocación effectful. Mantiene como
máximo un pendiente efímero por sesión; reemplazarlo cancela el anterior. La
máquina de estados y restricciones únicas de SQLite bloquean replay local de
aprobaciones y finales. Un reinicio no recupera propuestas como autorizaciones.
Si falla cualquier append anterior al efecto, no hay ejecución. Si falla el append
posterior, el resultado explicita que el efecto pudo ocurrir y el intent consumido
no se reintenta automáticamente. Esto no ofrece exactly-once distribuido ni
exclusión mutua frente a procesos o escritores externos.

Si falla `ACTION_APPROVED`, el pendiente continúa en `proposed`; si la aprobación
ya quedó durable pero falla `EXECUTION_STARTED`, continúa en `approved`. Dentro del
mismo proceso una nueva confirmación humana puede reintentar sólo el append faltante,
sin duplicar la aprobación. También puede cancelar un intent aprobado que aún no
comenzó. Tras reiniciar no se reconstruye ninguno de esos pendientes.

La aprobación sólo reconoce al actor humano. El modo conversacional `action`, el
modelo, la memoria, la tarea y cualquier contenido observado carecen de autoridad.
La auditoría no forma parte de `ContextProjection`, no se inyecta en prompts y no
se convierte en memoria. Laboral permanece en su política read-only existente y
no atraviesa aprobación effectful.

No existe un registry general de producción. Fuera de la única edición textual,
el contrato `EffectCapability` y los escenarios genéricos de `EffectResult` siguen
ejercitándose con un fake definido exclusivamente en tests. Tampoco existen grants,
ACL, roles, delegación persistente, retries, rollback general ni administración de
auditoría.

`AuditQueryService` expone una vista diagnóstica separada de los internals SQLite.
Su reader abre una base existente con `mode=ro` y `query_only`, lista intents,
resuelve ID o fingerprint no ambiguo y devuelve eventos ordenados. La secuencia se
valida sin corregirla: estados terminales y previos se derivan de eventos; una
secuencia imposible resulta `inconsistent`. `EXECUTION_STARTED` deja fase
`executing`, pero sin `EXECUTION_SUCCEEDED` o `EXECUTION_FAILED` el estado
observable es `execution_unknown`/interrumpido. La consulta no crea pending,
autoridad, retries, resume, memoria ni nuevos eventos.

La capability concreta es `filesystem_edit / apply_text_patch`. Su frontera pública
sólo prepara un intent validado y construye
un `ActionCoordinator` con una capability interna; la implementación que posee
`execute` no se exporta. No se agregaron escrituras a Laboral.

`FileEditScope` autoriza exactamente un archivo existente y absoluto. Bloquea
rutas relativas, `..`, streams alternativos de Windows y symlinks, junctions o
reparse points observables en el target y sus ancestros léxicos antes de resolver.
Vuelve a comprobar el path durante la ejecución. No afirma detectar una sustitución
externa posterior al último check ni ofrece garantías superiores a las APIs del
Windows/filesystem real. No admite scopes de directorio en esta versión.

La propuesta lee como máximo 1 MB de UTF-8, exige una única coincidencia y captura
el SHA-256 original. El expected text, replacement, hash, target, operación y
capability forman parte material del fingerprint. Los textos se redactan del
payload auditado, mientras el hash y metadatos no sensibles permanecen trazables.
El diff conversacional se limita a 8.000 caracteres; el intent conserva y valida
el patch completo. Si la vista se trunca, la CLI lo declara y bloquea el “sí” hasta
que el humano solicite la inspección read-only de los textos exactos de la
propuesta completa.

Tras `EXECUTION_STARTED`, la capability resuelve y lee nuevamente, verifica el
hash, genera el contenido final sin reinterpretar el patch aprobado y escribe un
temporal en el mismo directorio con flush/fsync. Antes del reemplazo guarda el
original en `data/action_backups/file_edits/<intent-id>-<fingerprint>/`, junto con
metadata que liga target y hash, y verifica la copia. Luego vuelve a comprobar la
precondición inmediatamente antes de `os.replace`. Esto reduce TOCTOU; no es un
lock. `os.replace` es el reemplazo portable elegido sin prometer garantías que el
OS o filesystem no ofrezcan. Audit registra referencia y hash del backup, nunca su
contenido. Como no existe restauración gobernada, `EffectResult.reversible` sigue
siendo falso: hay recuperación asistida, no undo.

### Backup y restauración de persistencias actuales

`core.backup.BackupService` es una fachada administrativa separada de los cuatro
dominios persistentes. Captura `conversations.db`, `memory.db`, `audit.db` y
`swarm.db` mediante la API de backup de SQLite, valida integridad y tablas
mínimas, calcula SHA-256 y escribe un manifiesto común. Un fallo parcial queda
registrado como `incomplete`; no se borra evidencia exitosa ni se inventa un
rollback entre bases.

La consistencia es por dominio. Las cuatro operaciones no comparten transacción,
por lo que el manifiesto niega explícitamente un snapshot global atómico. Verify
y restore aceptan únicamente manifiestos estructuralmente válidos y hashes
coincidentes. Restore crea un conjunto nuevo, byte a byte verificable y legible
por las fachadas existentes; no reemplaza bases activas, no reconstruye pending
de Audit y no activa adquisición de memoria. El storage de recuperación de
ediciones de C11 queda fuera como capa separada. No hay cifrado, incremental,
scheduler, cloud ni restore live.

### Frontera serial de turno

`miku.turn` contiene `TurnRequest`, `TurnResult` y `MikuTurnService`. La frontera
posee la coordinación reutilizable de un turno: persistencia de entrada/salida,
selección Laboral/Archive ya configurada, llamada a Miku, pending/errores de
memoria y error principal estructurado. Conserva `observed_source` en las
respuestas persistidas. No contiene `input()`, `print()`, Tk ni audio.
La lectura agrega el estado `partial` y un `read_outcome` efímero en `TurnResult`,
sin cambiar schemas de persistencia ni el retorno textual público de Miku.

La CLI posee parsing, lectura, presentación y lifecycle de consola/Pet. La
frontera es serial y rechaza reentrada; no introduce concurrencia, runtime
longevo, jobs ni protocolo remoto. `TurnRequest.source` registra procedencia y no
representa autoridad: un request arbitrario, incluso con `source="human"`, nunca
resuelve una aprobación effectful. `handle(request)` procesa solamente contenido;
la operación separada `handle_human(request)` debe ser invocada por una frontera
que haya observado interacción humana directa. La CLI interactiva cumple ese rol
y recién entonces el texto inequívoco llega al `ActionCoordinator` configurado.
La ampliación de scope exige también `handle_human`; la CLI lo usa para `/allow`
obtenido por teclado. El contenido o metadata de un request ordinario no amplían
permisos y el resume no reconstruye scopes.

La confirmación de memoria usa la misma frontera humana, pero conserva su pending
y política propios: una confirmación de memoria no aprueba Actions, y una
respuesta consumida por Actions no confirma memoria. Una futura interfaz STT/TTS
puede reutilizar el turno genérico, pero no adquiere autoridad effectful sin una
frontera explícita adicional. No se agregó un presupuesto agregado por turno: los
límites existentes viven en ContextManager, Laboral, Archive y el contrato de
lectura S2. Los contadores del loop no incluyen la extracción posterior de memoria
ni interrumpen operaciones bloqueantes; tiempo y volumen agregados siguen pendientes.

### Interfaz productiva de voz

`miku.voice` separa `SpeechInput`, `SpeechOutput` y `VoiceLoop`. El loop implementa
push-to-talk de consola (Enter, una captura, un turno), presenta la transcripción
antes de crear `TurnRequest(source="voice")` y entrega la respuesta visible a TTS
sólo después de recibir `TurnResult`. Nunca llama `handle_human`; un pending de
Actions o memoria permanece intacto y se deriva a la sesión de consola por ID.

Los contratos y fakes no requieren audio. Los adapters productivos son opcionales
y lazy: `SpeechRecognitionInput` captura por micrófono pero usa Google Speech
Recognition remoto para STT; `Pyttsx3Output` usa el motor TTS local del sistema.
Fallar STT descarta sólo esa captura y fallar TTS no pierde el texto. Activity es
best-effort y emite `listening`, luego el `thinking`/`error` propio de Miku,
`speaking` y finalmente `idle`. Con Pet, el grafo completo de voz se compone una
sola vez en main antes de iniciar el worker: sesión, repository, provider,
memoria, ContextManager, Miku y TurnService son los mismos objetos que ejecutará
el loop. Sólo la ejecución bloqueante pasa al worker y Tk permanece en el hilo
principal. No hay wake word, always-listening, daemon,
streaming, identificación de hablante ni autoridad por voz.

El adapter conserva un recognizer por sesión, calibra ruido ambiente una sola vez
durante 0,5 segundos y distingue timeout sin frase, audio incomprensible y fallo
de captura/backend. La locale predeterminada es `es-AR`, configurable por CLI o
entorno. El output pyttsx3 puede enumerar voces y seleccionar ID/índice y rate;
`--no-tts` usa un output nulo sin cargar TTS. El modo diagnóstico opt-in expone
únicamente STT, texto del request y texto del result, nunca el prompt compuesto.
El diagnóstico separado `--context-debug` usa el mismo observer seguro de la
proyección en voz, voz+Pet y chat; Pet no cambia su contenido.

La investigación manual confirmó en la base persistida el texto exacto y
`interface_source="voice"`. Tests con provider capturador verifican que cada
mensaje llega a `CONVERSACIÓN ACTUAL`, que dos turnos no comparten request o
result y que TTS recibe el result vigente. Un segundo dogfooding reveló una
asimetría: sin Pet el grafo se construía en main; con Pet, la callback lo construía
dentro del worker. Aunque la sesión ya se resolvía una sola vez, provider, memoria
y contexto no compartían la misma frontera de inicialización. Esa diferencia fue
eliminada y un test de paridad compara sesión, policy, historial, memoria,
conversación actual, task y prompt semántico con y sin Pet. Por eso ya no se
atribuyen las respuestas divergentes al provider sin la nueva prueba manual.

La vista Tk libera el root en `finally`, incluso si falla construir la vista;
cerrarla repetidamente es idempotente. Runtime recibe `KeyboardInterrupt`, pide
parada y espera el Event de finalización del worker, sin cerrar la sesión desde
main. Un fallo gráfico conserva la consola. Una interrupción escapada del worker
informa que el cierre no fue confirmado, sin traceback de threading.

En el recorte S1.5, `VoiceLoop.run()` ejecuta una vez por instancia y devuelve si
el cierre quedó confirmado. Si el turno no retorna, no intenta otra escritura
para cerrar; si termina y se interrumpe presentación/TTS, sí puede cerrar. La CLI
no imprime despedida de éxito cuando el resultado es incierto. La frontera de
turno serializa sus transiciones mediante un lock local: impide cerrar durante
un turno, duplicar cierres o continuar desde una instancia cuyo cierre empezó.
Persistencia sigue precediendo a RAM; una reanudación explícita usa otra instancia.
No se implementa reconciliación de commits ni cancelación de operaciones externas.

---

## Próxima evolución ratificada

Estado de implementación: Cambios 1 a 15 implementados. La secuencia
ratificada siguiente se conserva como referencia; las capacidades posteriores
siguen pendientes.

```text
1. base ejecutable y reproducible
   + configuración sencilla
   + proveedor sustituible
   + tests mínimos del núcleo conversacional

2. contexto alineado con esta taxonomía

3. memoria personal mínima y real de Miku

4. primera capacidad real:
   inspección read-only de archivos/proyectos,
   inicialmente apoyada en GNOMO-LABORAL

5. auditoría separada antes de acciones con efectos reales

7. primera capability effectful real:
   reemplazo textual exacto sobre un archivo existente y autorizado
```

Archive read-only fue implementado como Cambio 6 después de esa secuencia.
La Pet productiva opcional fue implementada como Cambio 8.
Consentimiento/consistencia de memoria y robustez de Activity/Pet se consolidaron
como Cambios 9 y 10, sin ampliar capabilities ni adelantar infraestructura futura.
Backup/restauración local y la frontera serial reutilizable se consolidaron como
Cambios 13 y 14, sin crear runtime persistente ni autoridad nueva. Cambio 15
agregó voz push-to-talk opcional sobre esa frontera, también sin autoridad nueva.

---

## Reglas arquitectónicas vigentes

1. La identidad no pertenece al modelo.
2. Los habitantes no son herramientas ni proveedores.
3. La República es propietaria de memoria y contexto.
4. Persistencia no implica memoria; auditoría no es memoria.
5. Miku coordina dentro de permisos; las capacidades producen efectos.
6. La autoridad se concede por capacidad y efecto.
7. La evidencia tiene prioridad sobre la opinión, sin sustituir revisión o autoridad.
8. El humano conserva el control final.
9. La República debe conservar capacidad local real y poder funcionar parcialmente sin IA.
10. Proveedores y modelos deben ser sustituibles y seleccionables explícitamente antes de introducir routing automático.
11. Las responsabilidades son permanentes; las capacidades evolucionan.
12. No agregar complejidad sin necesidad real.
13. Una capacidad compartida debe evitar duplicar lógica entre CLI y Miku.
14. Estado actual, próxima evolución y futuro deben documentarse sin confundirse.
15. Todo cambio material debe evaluar su impacto sobre canon y documentación operativa.

---

## Coherencia entre canon e implementación

Un cambio de código puede:

- implementar el canon vigente;
- modificar comportamiento operativo sin cambiar el canon, actualizando la documentación de uso;
- revelar que el canon necesita cambiar.

En el tercer caso, la modificación canónica debe presentarse como propuesta separada y requiere ratificación humana. Una implementación no puede convertir silenciosamente una preferencia técnica en principio, responsabilidad o política de la República.

---

## Decisiones deliberadamente abiertas

- Mecanismo exacto de selección y routing de modelos.
- Representación física de memoria, auditoría e invariantes.
- Forma de despliegue de cada habitante.
- Implementación del sistema de permisos.
- Concilio, jobs, voz avanzada y distribución completa.

Estas decisiones se tomarán cuando una necesidad cotidiana aporte evidencia suficiente.

---

## Frase guía

> Las responsabilidades son permanentes. Las capacidades evolucionan.
