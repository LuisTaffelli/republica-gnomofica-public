# Mini parlamento: ensayo opt-in

Prototipo temporal y aislado. No es un Concilio operativo ni incorpora identidades
persistentes: los tres participantes son roles de ensayo. No cambia el canon.
No se integra al arranque de Miku, a la UI ni a conversaciones reales.

Desde la raíz del repositorio (o del staging entregado):

```sh
python -B -m cli.parliament_cli --mode demo
python -B -m cli.parliament_cli --mode demo --rounds 1 --topic 'Ensayo de etiquetas'
python -B -m unittest discover -s tests -p test_parliament.py -v
```

En terminal, el comando muestra por defecto un acta legible en UTF-8, ajustada
al ancho disponible (20–120 columnas), con tema, participantes, evidencia,
rondas, argumentos, objeciones, incertidumbre, conclusión y reservas.
Los caracteres de control del contenido se sustituyen por espacios al presentar.
No utiliza colores ni secuencias de control.

`--format human` fuerza esa vista incluso al redirigir. `--format json` conserva
el recibo JSON original, estable y con escapes ASCII, para consumidores automáticos.
El modo automático conserva también JSON cuando stdout es un pipe o archivo:

```sh
python -B -m cli.parliament_cli --mode demo --format human
python -B -m cli.parliament_cli --mode demo --format json
```

La salida estructurada es un único recibo JSON. `result.label` identifica explícitamente el
modo. DEMO es un guion determinista con evidencia ficticia E1 y la regla pública E2 (Constitución, artículo I), sin inferencia;
cambiar el tema cambia la propuesta registrada, no convierte el guion en un
debate adaptativo. La segunda ronda incluye una respuesta escrita a la objeción
de construcción. Se muestran sólo argumentos públicos, nunca razonamiento privado.

Tres roles fijos: archivo (procedencia), exploración (alternativas) y construcción
(viabilidad). Cada turno contiene argumento, objeción, incertidumbre, postura y
referencias a evidencia disponible. La conclusión es mecánica: conserva últimas
posturas y reservas por participante; no adjudica ganadores ni ejecuta decisiones.
Una coincidencia de posturas no acredita verdad ni elimina reservas.

El prompt asigna una tarea pública concreta: Archivo distingue datos de supuestos;
Exploración propone una alternativa y una limitación; Construcción propone un
criterio observable de éxito y una condición de viabilidad. En ronda 2 se pide
nombrar a otro participante, resumir su objeción y responderla dentro de
`argumento`, con evidencia o incertidumbre explícita. La abstención sigue permitida;
no se fuerza consenso ni desacuerdo. Son instrucciones al modelo, no garantías
semánticas del validador. El guion de demo permanece determinista e inalterado.

## Vía local experimental

Sólo después de disponer de un modelo instalado y Ollama local ya iniciado:

```sh
python -B -m cli.parliament_cli --mode local --model NOMBRE_INSTALADO --timeout 60
```

La entrega inicial no ejecutó este comando; posteriormente se realizaron los dos
ensayos acotados descritos abajo. Usa ParliamentOllamaProvider sobre el transporte
acotado compartido con DelegatedOllamaProvider: endpoint fijo 127.0.0.1:11434, think=false, 256 tokens por
llamada, sin herramientas, redirects o reintentos. No descarga modelos ni inicia
servicios. Sólo recibe el tema, evidencia sintética y turnos públicos del ensayo;
no carga perfiles, configuración personal, memoria o bases. Un mismo modelo
interpreta roles; esto no acredita deliberación de habitantes reales.

El adaptador del Parlamento solicita directamente un objeto con los cinco campos
mediante `format` JSON Schema: textos de 1–160 caracteres, postura enumerada y
hasta dos referencias E1/E2. Ya no pide JSON serializado dentro de `answer`.
El contrato TextProvider sigue devolviendo texto JSON y el recibo público no
cambia. Otros consumidores de DelegatedOllamaProvider conservan `answer:string`.
Se extrae únicamente el transporte acotado `_generate`; no se añade endpoint,
configuración personal, herramientas, retries ni persistencia.

El esquema de generación no sustituye a `validate`: unicidad de referencias,
texto no vacío tras strip, controles, Unicode, límites de 1024 caracteres y
coherencia de campos siguen comprobándose localmente. La calidad de argumentos
y respuestas a objeciones requiere revisión humana. E1/E2 siguen siendo la misma
evidencia fija; no se ingieren archivos.

## Límites y cancelación

- Una o dos rondas, tres participantes: tres o seis llamadas como máximo.
- Tema de hasta 240 caracteres, respuesta hasta 1024, tres campos públicos de
  hasta 160 cada uno. Referencias permitidas: E1/E2, sin duplicados; pueden omitirse. Sin controles de terminal.
- Plazo total 30 segundos por defecto, configurable entre 0.1 y 120.
- El supervisor existente limita salida a 16384 bytes y mata/confirma la salida
  del worker al vencer el plazo o recibir Ctrl+C; salida incompleta no es éxito.
- Ctrl+C retorna 130; timeout 4; entrada inválida 2; fallo de turno 3;
  fallo de supervisor 5; límite/fallo de salida 6. Sin reintentos automáticos.
- El corte del cliente NO confirma cancelación de generación en Ollama.
  En timeout/cancelación del supervisor se pierde el acta parcial en RAM.
- `deliberate` es núcleo síncrono con cancelación cooperativa entre llamadas;
  un provider bloqueado sólo se interrumpe usando la CLI supervisada.
- No se usa persistencia ni capacidades con efectos. El JSON se imprime; guardarlo
  por redirección es una acción explícita del operador. La validación estructural
  no garantiza veracidad ni cumplimiento semántico de un modelo local.

## Evidencia de la entrega inicial

16 tests unittest aprobados: formato humano/JSON, selección por terminal, UTF-8, líneas y control de caracteres, cita de la regla pública y decisión pendiente, determinismo, reservas, cantidad de turnos/llamadas,
entrada y respuesta inválidas, referencias desconocidas, transmisión de objeciones,
fallo sin retry ni filtración de error, cancelación, adaptador local con llamada bloqueada simulada y guardia de
IO de demo (sin red, SQLite, archivos personales ni escrituras).

No se ejecutó inferencia ni la suite completa. La entrega agrega solamente CLI,
tests, esta guía y bitácora. Timeout y Ctrl+C se prueban atravesando el worker
local con ask reemplazado por una espera bloqueante, sin red ni modelo real.
El supervisor termina el proceso completo y confirma su salida antes de retornar.

## Comparación local del 2026-10-15

Mismo caso ficticio, Ollama 0.40.4 y qwen3:4b Q4_K_M (digest
`359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`),
think=false, temperatura 0, contexto 4096, máximo 256 tokens por llamada,
tres roles, dos rondas, seis llamadas como máximo y plazo total 120 s.

- Primer ensayo: completed, seis turnos válidos en 42,71 s; tres abstenciones,
  argumentos repetitivos y sin respuesta sustancial a objeciones.
- Segundo ensayo, tras concretar las instrucciones: invalid_or_failed_response,
  código 3, dos llamadas y una intervención válida en 3,79 s. No alcanzó la
  segunda ronda y no demuestra mejora deliberativa. Las dos peticiones HTTP
  devolvieron 200; el log muestra 104 y 142 tokens y truncated=0. No se conserva
  la respuesta rechazada ni la excepción específica: no se puede determinar
  el campo o capa exactos del rechazo sin instrumentación y otra prueba autorizadas.
- 17 pruebas pertinentes aprobadas, incluidos los 16 casos previos, mayor
  cobertura del historial/instrucciones por rol y aceptación de todas las abstenciones.
  Cancelación y timeout siguen siendo pruebas simuladas, sin inferencias adicionales.

Evidencia local: `.local-inference/evidence/` conserva la primera sesión;
`.local-inference/evidence/session2/` conserva el acta incompleta, JSON, logs,
pruebas y comparación. El rótulo fijo «inferencia local no validada» se mantiene
por compatibilidad; no sustituye estos resultados concretos. Los servidores
temporales se apagaron al terminar, sin acciones derivadas de las actas.
No se ejecutó la suite completa ni se modificaron canon o perfiles.

## Diagnóstico seguro y tercera sesión (2026-10-15)

Los fallos conservan estado `invalid_or_failed_response`, código de salida 3 y
los mismos criterios de rechazo. Sólo en fallo de provider/validador se agrega
`result.diagnostico` con cuatro campos: `etapa`, `rol`, `ronda`, `categoria`.
La vista humana muestra esos mismos metadatos. Las categorías son fijas:
`provider_transport`, `provider_response_invalid`, `provider_limit`,
`turn_json_invalid`, `turn_fields_invalid`, `turn_text_invalid`,
`turn_position_invalid`, `turn_evidence_invalid`, `unexpected_failure`.
No se serializan prompts, respuestas rechazadas, excepciones ni tracebacks.
Éxito, cancelación y timeout no añaden este campo. No se modifica el adaptador
compartido, el prompt, el esquema de respuesta del modelo ni sus límites.

22 pruebas pertinentes aprobadas, incluidas fallas sintéticas del provider y
validador, HTTP 200 con cuerpo inválido sin red, categoría desconocida y excepción
cuya conversión a texto falla. Se comprueba ausencia de marcador privado en JSON
y vista humana, una sola llamada fallida y conservación de turnos válidos.
Verificación adicional offline: seis prompts idénticos a sesión 2, mismo resultado
demo y 77 casos con idéntica aceptación/rechazo frente al validador anterior.

Una única tercera sesión real con el mismo caso/modelo/parámetros terminó en
3,73 s, código 3, dos llamadas y un turno válido. Diagnóstico:
`validator / exploracion / ronda 1 / turn_text_invalid`.
El adaptador aceptó la respuesta exterior: por tanto comprobó `done_reason=stop`,
texto UTF-8 no vacío y máximo 1024 caracteres. El JSON interior y sus claves
pasaron antes del rechazo de texto. Uno de `argumento`, `objecion` o
`incertidumbre` incumplió tipo, contenido no vacío, máximo 160 caracteres o
caracteres permitidos; la categoría no identifica cuál ni la condición concreta.
No se conservó el contenido rechazado. No es prueba de la causa exacta de sesión 2.
El turno válido de Archivo coincide con el de sesión 2. No llegó a ronda 2.
HTTP 200 en ambas llamadas; log: 104/142 tokens generados y truncated=0.
Evidencia nueva en `.local-inference/evidence/session3/`; anteriores intactas.
Servidor detenido y puerto cerrado; sin reintentos reales ni relajación de límites.

## Resolución dirigida de formato (2026-10-15)

El diagnóstico de texto incorpora sólo `campo` y `regla` de listas fijas:
`response/argumento/objecion/incertidumbre` y
`type/empty/length/control/unicode_surrogate`. Para claves, campo `turn` con
`type/missing_fields/extra_fields/fields_mismatch`; para límite de generación,
`response/generation_tokens`. Nunca incluye nombres de claves recibidas,
longitudes concretas, texto rechazado, prompt o excepción.

| Sesión | Cambio probado | Resultado |
| --- | --- | --- |
| 4 | Diagnóstico por campo/regla, prompt igual a sesión 3 | 1 turno válido, 2 llamadas, 3,69 s; exploracion/ronda 1/argumento/length |
| 5 | Más instrucciones de brevedad | 0 turnos, 1 llamada, 3,68 s; provider_limit y 256 tokens en log |
| 6 | Instrucción compacta | 0 turnos, 1 llamada, 2,58 s; turn_fields_invalid |
| 7 | Enumeración inequívoca de cinco campos | 5 turnos, 6 llamadas, 8,12 s; construccion/ronda 2/argumento/length |
| 8 | Esquema nativo de cinco campos, mismo validador | completed, 6 turnos, 6 llamadas, 7,31 s |

La sesión 4 demuestra argumento demasiado largo en esa ejecución; no reconstruye
las respuestas rechazadas de sesiones anteriores. Los prompts solos no
aseguraron los límites. El esquema nativo resuelve el caso técnico observado,
pero no garantiza fiabilidad general. No se hizo recorte ni reparación automática
de respuestas: todo rechazo sigue cerrando la sesión, sin retry.

Calidad de sesión 8: tres abstenciones y 0/3 turnos de ronda 2 que identifiquen y
respondan explícitamente a otro rol. Hay textos defectuosos como «El catá: 160»,
E2 repetida como objeción y dudas que ignoran el encuadre ficticio. Exploración
propone etiquetas y Construcción menciona un criterio de éxito, sin un contraste
sólido de objeciones. El acta original conserva esas salidas. Éxito estructural
no equivale a deliberación útil ni a aprobación humana.

Regresiones offline finales: 213 passed, 2 skipped y 43 subtests passed (10,58 s):
Parlamento, adaptador nativo, transporte/turnos delegados y lectura/supervisor.
Omisiones: junction Windows y launcher editable ausente. No suite completa.
Evidencia: `.local-inference/evidence/session4/` hasta `session8/`;
comparación final en `.local-inference/evidence/resolution-20261015/`.
Cinco sesiones dirigidas, 16 llamadas totales; límites originales por sesión.
Sin más descargas ni cambio de modelo, permisos o servicio permanente; servidor
apagado y procesos propios ausentes. Actas anteriores y canon preservados.

Fuentes del esquema: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
y [llama.cpp JSON Schema/GBNF](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md).
El backend soporta longitudes de cadenas pero no toda la semántica JSON Schema;
por eso el validador sigue siendo obligatorio.


## Compatibilidad real del modelo: corrección del 15 de octubre

El GGUF instalado bajo qwen3:4b es **Qwen3-4B-Thinking-2507**, sólo thinking,
confirmado por metadata interna, hash íntegro y contrato oficial. No es la variante
original con conmutación de modos. Las pruebas anteriores quedan como evidencia
histórica de un contrato incompatible; no demuestran incapacidad general de4B.

ParliamentOllamaProvider consulta /api/show una vez por instancia. Si metadata
thinking.values no admite false, falla con provider_thinking_unsupported antes de
/api/generate. No cambia esquema, validación ni presupuestos. Metadata ausente en
servidores antiguos mantiene comportamiento previo, sin certificar compatibilidad.
La demo funciona; el modelo actual no debe usarse para este contrato think=false.
El contador de llamadas registra intentos del proveedor, no sólo generaciones.

223 passed,2 skipped,43 subtests passed; cero inferencias nuevas en esta revisión.
Detalles, límites y opciones que requieren autorización en
[PARLIAMENT_MODEL_COMPATIBILITY.md](PARLIAMENT_MODEL_COMPATIBILITY.md).


## Seguimiento: Instruct probado el 15 de octubre de 2026

Con autorización de Visitante se instaló y verificó `qwen3:4b-instruct-2507-q4_K_M`,
conservando Thinking. Doce llamadas reales terminaron con JSON válido, pero los
controles de calidad fallaron: tres textos incompletos entre seis contextos y
cuatro errores aritméticos entre seis casos de roles. No se ejecutó otra sesión
encadenada ni se cambió la selección de modelo o código productivo. Servidor apagado.
Resultados, hashes, límites, disco y evidencia en
[PARLIAMENT_INSTRUCT_RESULT.md](PARLIAMENT_INSTRUCT_RESULT.md).


Cuatro controles posteriores separan cierre bajo esquema y aritmética: el texto
libre termina pero incumple límites; el cálculo falla también libre y sin contexto.
No hay cambio productivo. Véase [PARLIAMENT_MINIMAL_CONTROLS.md](PARLIAMENT_MINIMAL_CONTROLS.md).


La comparación aislada de longitud y prosa obligatoria no produjo una variante
que cumpla los criterios de promoción. Producción sin cambios. Véase
[PARLIAMENT_CONTRACT_EVALUATION.md](PARLIAMENT_CONTRACT_EVALUATION.md).


El diagnóstico focalizado de voces mejora dos roles pero no supera el umbral
previo de promoción. Se cierra la serie sin cambios productivos; detalles y casos
completos en [PARLIAMENT_VOICE_DIAGNOSTIC.md](PARLIAMENT_VOICE_DIAGNOSTIC.md).
