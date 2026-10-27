# Roadmap de la República Gnomófica

## Cómo leer este roadmap

El roadmap expresa prioridad, no destino inevitable ni calendario. La República crece cuando una capacidad resuelve una necesidad real.

El estado heredado y el futuro se distinguen de la próxima etapa para no confundir código existente con arquitectura imaginada.

---

## Estado heredado

La República ya posee:

- una CLI común;
- conversación persistente con Miku;
- sesiones, mensajes y ramas;
- un primer sistema de perfiles y contexto;
- GNOMO-ARCHIVE con operaciones locales sobre SQLite;
- GNOMO-LABORAL como inspector experimental;
- Ollama como único proveedor operativo actual.

También conserva piezas experimentales o incompletas: resúmenes desconectados,
un modo `action` limitado y una fachada transitoria de contexto. La memoria
personal ya es operativa y permanece separada del historial y de Archive.

Todavía no existen permisos granulares, routing, jobs, Concilio operativo ni
swarm distribuido. La voz productiva inicial ya existe como push-to-talk; no
incluye wake word, escucha permanente ni autoridad. La auditoría previa y una primera edición textual effectful
explícita ya pertenecen al estado implementado.
La recuperación acotada de esa edición, la consulta read-only de Audit y la Pet
productiva opcional de Miku también pertenecen al estado implementado.

---

## Próxima etapa — Miku cotidiana

### Cambio 1 — Base ejecutable y reproducible

Objetivo: que el núcleo conversacional pueda instalarse, configurarse y verificarse sin depender de conocimiento implícito del entorno.

Incluye:

- versión moderna concreta de Python;
- dependencias declaradas de manera estándar y simple;
- ejecución reproducible desde el repositorio;
- configuración sencilla de provider/modelo/endpoint;
- contrato sustituible de proveedor;
- tests mínimos del núcleo conversacional sin requerir un modelo real.

No incluye rediseño de contexto, memoria, Laboral, tools, Archive, auditoría, routing ni distribución.

### Cambio 2 — Contexto alineado con el canon

Objetivo: distinguir invariantes, identidad, memoria, conocimiento compartido, conversación y tarea sin cargar documentos indiscriminadamente ni ceder contexto al modelo.

### Cambio 3 — Memoria personal mínima y real

Objetivo: que Miku conserve recuerdos pertinentes entre conversaciones según categorías y autoridad, sin confundirlos con historial, Archive o auditoría.

Estado: implementado. La adquisición implícita usa extracción selectiva y política
de persistencia; la recuperación inicial es textual y acotada, sin RAG ni vectores.

### Cambio 4 — Primera capacidad real de Miku

Objetivo: que Miku pueda solicitar inspección read-only de archivos o proyectos explícitamente indicados y usar el resultado como evidencia para responder.

La implementación inicial se apoyará en la responsabilidad y capacidad existente de GNOMO-LABORAL. Observar no implica modificar ni ejecutar comandos arbitrarios.

Estado: implementado. Laboral ofrece capacidades estructuradas de árbol, lectura,
búsqueda y AST Python bajo scope read-only explícito. El corte operativo S2.2
conecta sólo lectura al chat con `--inspect`; las cuatro se conservan por las vías
directas de Laboral. El loop JSON acepta final sin lectura y conserva resultados
parciales, bajo límites de decisiones/lecturas y sin promoción a memoria o Archive.
El ajuste de compatibilidad envía un esquema por llamada sólo en inspección;
un smoke sintético con el modelo local completó saludo, lectura fundamentada y
negativa del modelo fuera de scope. No certifica fiabilidad general ni una
denegación de Laboral ejecutada durante ese smoke.
La reincorporación de las otras capabilities al chat está pendiente en la guía
operativa; no amplía este objetivo canónico.

### Cambio 5 — Auditoría antes de efectos

Objetivo: separar evidencia de acciones de la memoria antes de incorporar capacidades con efectos reales.

No exige todavía jobs, permisos granulares ni un framework general de herramientas.

Estado: implementado. `ActionIntent`, `ActionPolicy` y `ActionCoordinator`
establecen propuesta durable antes del efecto, aprobación humana ligada al
fingerprint exacto, protección local contra replay y evidencia de resultado en
`data/audit.db`. La integración conversacional admite un único pendiente efímero
por sesión y el modo `action` no concede autoridad. Sólo se usan capabilities
effectful falsas en los tests propios de ese cambio; la primera capability real
se incorpora separadamente en Cambio 7.

### Cambio 6 — Archive read-only desde Miku

Objetivo: permitir que Miku consulte la biblioteca compartida sin convertir
Archive en memoria personal ni inyectarlo completo en cada conversación.

Estado: implementado. Miku consulta Archive sólo mediante activación explícita,
con búsqueda, lectura y vínculos estructurados, acotados y read-only. Los
resultados conservan procedencia y viven únicamente en contexto temporal de
tarea; no se convierten en memoria personal, autoridad o canon y Archive no se
inyecta en conversaciones ordinarias.

### Cambio 7 — Primera capability effectful real

Objetivo: modificar de forma controlada un único archivo existente sin abrir una
vía general de escritura.

Estado: implementado. `apply_text_patch` valida un scope exacto, captura la
precondición SHA-256, presenta un diff, exige aprobación humana por el intent
exacto y escribe mediante temporal local + `os.replace`. Los cambios concurrentes
bloquean el efecto cuando invalidan la precondición observada; la evidencia
conserva hashes y tamaños. No incluye creación, borrado, directorios, shell, Git
ni undo general. Cambio 11 agregó recuperación acotada sin volverla un editor.

### Cambio 8 — Pet productiva de Miku

Objetivo: dar a Miku una presencia visual ligera que represente actividad real
sin incorporar cognición, memoria, autoridad ni detalles internos del core.

Estado: implementado. Una frontera neutral de actividad con sink nulo desacopla
al core de Tkinter. La Pet opcional consume eventos mediante una cola local, deja
el main loop de Tk en el hilo principal y representa conversación, inspección,
aprobación, ejecución y errores. Windows intenta una ventana flotante transparente
por color-key con fallback portable; `listening` y `speaking` están preparados
pero todavía no se emiten productivamente.

### Cambio 9 — Consentimiento y consistencia de memoria

Estado: implementado. `--no-memory` es un override efímero también al reanudar,
las confirmaciones son conservadoras y resuelven un candidato exacto por vez, las
correcciones relacionadas son atómicas dentro de `memory.db` y los fallos de
memoria se diagnostican sin perder la respuesta principal.

Follow-up de dogfooding: la política suma una defensa determinista acotada para
salud obvia, los recuerdos se proyectan con sujeto humano explícito y el provider
puede auditarse mediante `--context-debug` seguro. La evidencia parcial de la
última inspección Laboral queda reutilizable sólo durante el proceso, sin DB ni
promoción a memoria.

Segundo follow-up de dogfooding: candidatos equivalentes a recuerdos active ya
no generan duplicados ni pending, referencias inequívocas al turno inmediato se
descartan antes de policy y existe una limpieza administrativa dry-run/apply por
soft-retire. Ollama transmite `think` como opción estructurada configurable,
desactivada por defecto para Miku conversacional. No se agregó routing ni una
política acoplada a un modelo.

### Cambio 10 — Robustez de Activity/Pet y lifecycle

Estado: implementado. Una frontera best-effort impide que cualquier sink altere
provider, Laboral, Archive o Actions. Cerrar la Pet desactiva la vista y descarta
eventos futuros sin abortar el trabajo; la cola es acotada, Tk permanece en el
hilo principal y los fallos gráficos o del worker tienen diagnóstico controlado.
Follow-up S1.5 (2026-10-04): Ctrl+C solicita parada cooperativa y espera la
finalización del worker; cerrar la ventana sigue siendo sólo visual. Tk libera
sus recursos aun fallando la construcción y el cierre visual es idempotente.

### Cambio 11 — Safe file edit revisable y recuperable

Estado: implementado. Cada ejecución conserva primero el original en storage
interno ligado a intent, fingerprint y SHA-256; Audit guarda sólo referencia y
hash. Los diffs truncados requieren inspección completa antes del “sí”, los límites
de bytes se aplican antes de leer el contenido y el scope comprueba reparse points
en ancestros observables. No hay restauración gobernada: `reversible=False`, con
recuperación asistida y sin locking frente a escritores externos.

### Cambio 12 — Consulta read-only de Audit y estados interrumpidos

Estado: implementado. `audit list` y `audit show` usan una fachada SQLite
read-only para exponer intents, eventos ordenados, evidencia acotada y estados
derivados. Un inicio sin terminal es `execution_unknown` con fase `executing`; una
secuencia imposible es `inconsistent`. Consultar no autoriza, reconstruye pending,
reintenta, reanuda ni inyecta Audit en Miku.

### Cambio 13 — Backup y restauración de dominios persistentes

Estado: implementado. Una fachada administrativa captura conversaciones, memoria,
Audit y Archive mediante snapshots SQLite independientes, manifiesto con tamaño y
SHA-256, verificación de integridad y restauración a un directorio nuevo. No hay
atomicidad global, overwrite de bases activas, restore live ni inclusión de los
artefactos de recuperación de ediciones de C11.

### Cambio 14 — Frontera mínima de turno reutilizable

Estado: implementado. `TurnRequest`/`TurnResult` y un servicio serial extraen de
la CLI persistencia, memoria, contexto y selección Laboral/Archive. La CLI queda
como adaptador de argumentos, entrada, presentación y lifecycle. La frontera no
incluye IO de consola, UI, audio, runtime ni jobs. Como corrección de integración,
`source` queda explícitamente limitado a procedencia: `handle(TurnRequest)` no
concede autoridad aunque el source diga `human`. La aprobación y la confirmación
de memoria sólo entran por `handle_human`, operación separada que usa la CLI tras
leer interacción directa. Una futura voz puede reutilizar el turno entre STT y
TTS, pero no hereda autoridad effectful automáticamente.

Follow-up de persistencia (2026-10-03): el esquema conversacional v2 desempata
timestamps mediante un ordinal durable por sesión, conservando UUID y anclas.
La migración preserva el orden visible v1, requiere activación explícita y no se
ejecuta al abrir la aplicación. Regresión y migración sólo sobre bases sintéticas:
513 tests aprobados. En ese checkpoint no se modificaron bases personales ni canon.
El 2026-10-04 se autorizó y verificó el reinicio definitivo sin respaldo nuevo
de conversaciones (v2), memoria y Archive: datos vacíos e integridad comprobada,
con perfiles/configuración, respaldo antiguo y exportación conservados.
Ver [registro del reinicio](../.republic/changes/2026-10-04-reiniciar-conversaciones-memoria-y-archive.md).
Ver [bitácora](../.republic/changes/2026-10-03-orden-durable-del-historial-y-migracion-explicita.md).

### Cambio 15 — Voz productiva push-to-talk de Miku

Estado: implementado. `miku voice` captura una frase por activación explícita,
muestra la transcripción, la procesa sólo mediante `MikuTurnService` y conserva
la respuesta textual antes de TTS. Los contratos y fakes no dependen de hardware;
los adapters opcionales usan Google Speech Recognition remoto para STT y
`pyttsx3` local para TTS. La Pet refleja escucha, pensamiento y habla sin tocar Tk
desde el worker. STT nunca invoca `handle_human`: aprobaciones effectful y
confirmaciones sensibles de memoria se derivan al teclado confiable.

Follow-up de integración: la traza persistida y un provider capturador confirmaron
que las transcripciones llegan intactas al prompt y que no se reutilizan requests,
results ni respuestas entre turnos. Un segundo follow-up eliminó la asimetría de
ownership que componía el grafo de voz en main sin Pet y dentro del worker con
Pet; ahora se construye una vez antes del worker y ambos modos tienen paridad
testeada de sesión, historial, memoria y contexto. Ctrl+C también cierra Tk sin
traceback. STT diferencia errores, conserva `es-AR` configurable y calibra ruido
una vez. TTS permite listar/seleccionar voces y rate, o desactivarse sin perder la
salida textual. La validación final requiere comparar nuevamente voice y
voice+Pet con micrófono y speaker reales.

Follow-up S1.5 (2026-10-04): voz distingue salida segura de turno incierto y no
cierra desde `finally` tras una escritura fallida o interrumpida. Cierre único
por instancia, persistencia antes de RAM y parada solicitada desde Pet verificados
con hilos reales y bases sintéticas; 232 tests focales y 580 de suite completa
aprobados. No incluye cancelación inmediata, fallos anteriores a construir el
servicio ni un cambio de lifecycle de edición. Ver [bitácora](../.republic/changes/2026-10-04-cierre-coordinado-de-pet-y-voz.md).

---

## Horizontes futuros

Se mantienen como direcciones válidas, sin adelantar infraestructura:

- conocimiento relacional y reconstrucción histórica;
- capacidades con efectos y permisos más granulares;
- tareas largas, cancelables y reanudables;
- integración con servicios y agentes externos;
- STT local reemplazable y voz neural opcional, si la experiencia real lo justifica;
- acceso desde celulares y otros dispositivos;
- ejecución distribuida según capacidades;
- Concilio cuando la deliberación entre habitantes resuelva un problema real;
- routing automático cuando existan suficientes proveedores, tareas y experiencia de uso.

La República debe ser accesible desde múltiples dispositivos antes de necesitar ser completamente ejecutable en todos ellos.

---

## Apéndice — Símbolos nacionales

La bandera y el escudo siguen siendo asuntos legítimos de Estado, pero no bloquean la próxima etapa técnica.

La bandera deberá evocar una mezcla entre biblioteca, bosque y departamento de ingeniería. Debe funcionar en tamaños pequeños, no parecer una startup de blockchain y conservar lugar para un gnomo con sombrero chistoso.

El mate continúa pendiente de aprobación constitucional.

---

## Fase ∞

Seguir explorando.

No sabemos a dónde lleva esto.

Etiquetalo.
