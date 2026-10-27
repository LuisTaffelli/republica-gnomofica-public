# AGENTS.md — República Gnomófica

Este archivo define cómo deben trabajar los agentes de desarrollo sobre este repositorio.

Es estrictamente operacional. No forma parte del canon, no puede modificarlo y no introduce decisiones arquitectónicas nuevas.

## 1. Fuentes de autoridad

Antes de realizar cambios que puedan afectar arquitectura, comportamiento o responsabilidades, consultar los documentos relevantes:

- `docs/VISION.md`
- `docs/FOUNDATIONS.md`
- `docs/CONSTITUCION.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`

El README y demás documentación operativa describen el sistema existente. No deben interpretarse como autoridad para introducir decisiones canónicas.

Si existe conflicto entre una tarea solicitada y el canon vigente, detenerse, describir el conflicto y solicitar una decisión humana. No resolverlo modificando silenciosamente el canon o la implementación.

## 2. Cambio mínimo

Implementar el cambio más pequeño que satisfaga el objetivo solicitado.

No introducir infraestructura, abstracciones, frameworks o capacidades futuras que no sean necesarias para el cambio actual. Una mejora razonable pero fuera de alcance debe informarse como observación, no implementarse preventivamente.

## 3. Canon e implementación

Un cambio puede:

1. implementar una decisión canónica existente;
2. modificar comportamiento operativo sin alterar el canon;
3. revelar la necesidad de modificar el canon.

En el tercer caso, detenerse y presentar la modificación canónica como propuesta separada. Una implementación, una bitácora o un ADR nunca convierten por sí mismos una decisión técnica en canon.

## 4. Documentación

Todo cambio que modifique comportamiento, arquitectura o responsabilidades debe evaluar explícitamente su impacto documental.

Antes de finalizar:

- comprobar si cambió comportamiento observable;
- comprobar si cambió arquitectura o responsabilidades;
- actualizar documentación operativa cuando corresponda;
- identificar cualquier impacto sobre el canon;
- si no se actualiza documentación, explicar por qué no fue necesario.

## 5. Evidencia

No afirmar que un comportamiento funciona sin evidencia apropiada.

Cuando corresponda:

- ejecutar los tests relevantes;
- ejecutar la suite disponible;
- realizar verificaciones manuales necesarias;
- informar exactamente qué se verificó y qué no pudo verificarse.

No ocultar tests fallidos, servicios ausentes ni limitaciones del entorno.

## 6. Compatibilidad y estado existente

No preservar accidentalmente deuda técnica sólo porque existe.

Tampoco eliminar o migrar datos, cambiar esquemas, romper interfaces o realizar refactorizaciones amplias salvo que el objetivo del cambio lo requiera.

Cuando la compatibilidad sea relevante, debe provenir del alcance solicitado o del canon, no de una suposición.

## 7. Decisiones abiertas

No completar decisiones que el canon mantiene deliberadamente abiertas.

En particular, no introducir por anticipado arquitectura para routing, Concilio, permisos granulares, jobs, distribución, memoria, herramientas u otras capacidades futuras salvo que la tarea aprobada las requiera.

## 8. Bitácora local de cambios materiales

Todo cambio material debe dejar un registro breve y legible por humanos en `.republic/changes/`.

La bitácora explica el trabajo; no sustituye a Git, no duplica el diff y no crea canon. Cada registro debe permitir comprender:

- objetivo;
- motivo;
- decisiones y límites relevantes;
- evidencia o verificaciones realizadas;
- resultado;
- impacto sobre documentación operativa;
- impacto o ausencia de impacto sobre el canon;
- observaciones fuera de alcance, cuando existan.

El título debe describir el cambio de forma concreta y puede servir como base para un mensaje de commit. No enumerar mecánicamente cada línea o archivo: Git conserva el detalle exacto de las modificaciones.

## 9. ADR

Crear o proponer un ADR solamente cuando aparezca una decisión arquitectónica duradera, con alternativas y consecuencias relevantes, que no esté ya determinada por el canon o por un ADR vigente.

No crear ADR para decisiones triviales, locales, temporales o fácilmente reversibles. Un ADR no reemplaza la bitácora.

Un ADR registra una decisión arquitectónica y su razonamiento, pero no se convierte automáticamente en canon. Si contradice o pretende modificar el canon, debe presentarse como propuesta separada y esperar ratificación humana.

## 10. Entrega

Al finalizar una tarea, informar como mínimo:

- qué se modificó y por qué;
- qué tests o verificaciones se ejecutaron y sus resultados;
- qué no pudo verificarse;
- impacto sobre documentación operativa;
- impacto sobre canon;
- ubicación de la bitácora del cambio material, cuando corresponda;
- observaciones relevantes fuera de alcance.

No realizar commits, pushes, publicaciones, despliegues ni otros efectos externos salvo autorización explícita.
