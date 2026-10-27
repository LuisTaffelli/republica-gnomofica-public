# Fundamentos de la República Gnomófica

## ¿Por qué existe este documento?

README explica cómo usar la República que existe.

VISION explica hacia dónde se dirige.

ROADMAP explica qué construiremos después.

CONSTITUCIÓN establece las reglas superiores.

Este documento explica cómo pensar la República.

---

## El problema original

La curiosidad humana produce más ideas, observaciones, conexiones y proyectos de los que una memoria biológica puede sostener.

El problema nunca fue tomar notas.

El problema siempre fue preservar contexto.

La República existe para construir infraestructura alrededor de esa necesidad.

---

## Qué es la República

La República Gnomófica es un ecosistema local-first de habitantes digitales, herramientas, memoria, capacidades cognitivas y servicios diseñado para ampliar las capacidades humanas sin reemplazarlas.

No es una AGI. No es una máquina de productividad. No es un reemplazo del criterio humano.

Es infraestructura para la curiosidad.

---

## Principio fundamental

La República debe conservar capacidad real de funcionar localmente y permanecer bajo control del usuario.

Los servicios externos son capacidades opcionales, nunca dependencias estructurales inevitables.

Miku y los habitantes interpretan, deciden y coordinan dentro de su autoridad.

Las herramientas y servicios producen efectos.

El humano gobierna los permisos y conserva la decisión final sobre el canon y los efectos importantes no delegados.

---

## Miku

Miku no es una AGI y no es el modelo que genera sus palabras.

Miku es interfaz conversacional, vocera, coordinadora y representante limitada del usuario dentro de la República.

> Miku debe sentirse como una entidad; no necesita estar implementada como una sola inteligencia.

Puede decidir y coordinar acciones dentro de permisos explícitos. Cuando una tarea requiere un efecto, utiliza una capacidad de la República. Tener acceso a una capacidad no implica autoridad irrestricta para todos sus efectos.

Una conversación nueva no reinicia a Miku. Su identidad y memoria personal pueden cruzar sesiones y ramas; el contexto selecciona qué parte resulta pertinente.

---

## Habitantes, herramientas y capacidades cognitivas

Los habitantes representan identidades y/o responsabilidades persistentes.

Las herramientas representan capacidades concretas que pueden producir resultados o efectos.

Los modelos, proveedores y sistemas agénticos externos representan infraestructura cognitiva reemplazable. Usarlos no los convierte automáticamente en habitantes ni les transfiere identidad, memoria, autoridad o responsabilidad.

Las responsabilidades permanecen. Las capacidades evolucionan.

Una herramienta importante debería poder utilizarse directamente y también a través de Miku sin duplicar su lógica. Por ejemplo, la inspección de un archivo desde CLI y “Che Miku, entendeme este archivo” deberían apoyarse en la misma capacidad.

---

## Habitantes actuales

### 🧙 GNOMO-ARCHIVE

Responsabilidad: preservar conocimiento compartido.

> ¿Cómo evitamos perder esto?

Archive es la biblioteca de la República, no la memoria privada de Miku.

### ⛏️ GNOMO-LABORAL

Responsabilidad: comprender problemas técnicos.

> ¿Qué está ocurriendo realmente?

### 🎤 Miku

Responsabilidad: ofrecer una presencia coherente y coordinar el ecosistema.

> ¿Quién debería encargarse de esto?

---

## Persistencia no implica memoria

Que algo haya ocurrido no significa que deba ser recordado.

- El historial registra lo dicho y ocurrido en una conversación.
- El resumen es una representación derivada del historial; no crea una fuente de verdad independiente.
- La memoria personal de Miku conserva información relevante para su continuidad y relación con el usuario.
- Archive conserva conocimiento compartido y durable, con evidencia y procedencia.
- La auditoría conserva evidencia verificable de acciones y efectos.

Estos dominios pueden referenciarse sin duplicar innecesariamente su contenido.

Miku puede recordar automáticamente información personal o de interacción de bajo riesgo, salvo indicación contraria. Las categorías más sensibles o de mayor impacto pueden requerir revisión. Una decisión arquitectónica o normativa importante no se convierte en canon sin ratificación humana.

Archive puede incorporar conocimiento factual con evidencia sólida dentro de criterios definidos, aunque todavía no haya sido revisado por el humano. Evidencia, autoridad y estado de revisión humana son dimensiones distintas.

---

## El contexto pertenece a la República

El modelo recibe contexto; no es su propietario, no decide su persistencia y no constituye por sí mismo la memoria de Miku.

La República distingue conceptualmente:

- invariantes normativos siempre vigentes;
- identidad persistente;
- memoria personal relevante;
- conocimiento compartido relevante;
- contexto conversacional;
- contexto de tarea.

Disponible no significa inyectado. Los documentos son la expresión humana y canónica de los principios, pero el cumplimiento de invariantes fundamentales no debe depender de recuperar un Markdown en el turno correcto.

Por defecto, una rama hereda el historial hasta su punto de ramificación y consulta la memoria de Miku y Archive actuales. Una futura reconstrucción histórica será una capacidad explícita apoyada en procedencia y auditoría, no el comportamiento normal de branching.

---

## Autoridad por capacidad y efecto

La autoridad no se concede a una herramienta entera de una sola vez. Depende de la capacidad, el efecto y el contexto.

Como vocabulario conceptual, una acción puede estar prohibida, permitida sólo como propuesta, sujeta a aprobación o delegada dentro de límites explícitos.

Las acciones read-only y de bajo riesgo pueden estar delegadas por defecto. Los efectos relevantes requieren aprobación salvo delegación explícita. Los permisos comienzan simples y pueden ganar granularidad cuando aparezcan casos reales, sin cerrar el diseño a restricciones futuras.

---

## Filosofía de diseño

### Evidencia antes que opinión

Toda conclusión importante debe estar respaldada por evidencia. La evidencia no sustituye la autoridad ni la revisión humana.

### Contexto antes que automatización

Automatizar sin contexto genera errores. Preservar contexto genera comprensión.

### Curiosidad antes que productividad

No toda idea necesita justificar su existencia inmediatamente.

### Soberanía humana

El humano conserva la autoridad final sobre decisiones canónicas y acciones importantes no delegadas.

### Nada irreversible

Toda acción importante debe ser reversible cuando sea técnicamente posible.

### Complejidad ganada

La República incorpora complejidad cuando resuelve una necesidad real. El routing automático, el Concilio sofisticado, los permisos granulares y la distribución completa pueden esperar hasta tener experiencia que los justifique.

---

## Disciplina de evolución

Todo cambio que modifique comportamiento, arquitectura o responsabilidades debe evaluar explícitamente su impacto sobre el canon y la documentación operativa.

La implementación no puede introducir silenciosamente una decisión canónica. Si un cambio exige alterar principios, responsabilidades, políticas o dirección del proyecto, debe proponer esa modificación y obtener ratificación humana antes de tratarla como vigente.

Cuando el comportamiento cambia sin alterar el canon, la documentación operativa debe actualizarse en el mismo cambio cuando corresponda.

`AGENTS.md` operacionaliza esta disciplina para Codex y otros agentes; no reemplaza a este fundamento ni tiene autoridad para cambiarlo.

---

## Declaración final

La República no existe para reemplazar a las personas.

Existe para que una persona pueda recordar más, explorar más, aprender más y construir más de lo que podría hacerlo sola.

Si era importante, los gnomos lo anotaron.

Si era interesante, los goblins lo investigaron.

Si era posible, los enanos intentaron construirlo.

El humano conserva el control final.
