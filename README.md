# República Gnomófica — muestra pública

Un proyecto experimental de contexto local-first: conversación con Miku,
sesiones, memoria gobernada y capacidades con autoridad humana explícita.
Esta copia contiene un perfil nuevo de ejemplo y escenarios ficticios.

## Probar la muestra

Requisitos: Python 3.11 o posterior, `venv` y `pip`. Desde esta carpeta:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python demo.py
python -m cli.parliament_cli --mode demo
python -m pytest
```

Para incluir los tests HTTP instalá `python -m pip install -e '.[test,web]'`.
Los tests de la interfaz requieren Node.js y se ejecutan con
`node --test tests/web_ui.test.cjs`.

En Windows activá el entorno con `.venv\Scripts\Activate.ps1` en PowerShell.
La instalación inicial descarga dependencias; las dos demos se ejecutan sin red,
sin Ollama y sin claves. Usan respuestas predefinidas: demuestran el circuito
real de turnos y el ensayo de deliberación, no la calidad de un modelo.
La demo conversacional crea bases temporales y las elimina al finalizar.

## Conversar con un modelo local

Opcional: instalá Ollama desde su fuente oficial y descargá un modelo compatible
con `think=false`. El modelo predeterminado del código es
`hf.co/Qwen/Qwen3-8B-GGUF:Q5_K_M`; requiere una descarga grande y recursos locales.
Podés elegir otro con `REPUBLICA_MODEL` si respeta el contrato del provider.

```bash
ollama pull hf.co/Qwen/Qwen3-8B-GGUF:Q5_K_M
python main.py miku chat --temporary --no-memory
```

La CLI normal puede crear bases vacías en `data/`, incluso usando una sesión
temporal. Las sesiones persistentes y la memoria se habilitan con otros modos;
cualquier dato introducido después de descargar esta muestra pertenece a quien
la ejecute. Revisá nuevamente el contenido antes de redistribuir una copia usada.

## Contenido y límites

- Código Python, interfaz web existente y tests del runtime.
- Perfil `miku` creado desde cero, sin recuerdos ni identidad de una persona real.
- Documentos de visión, fundamentos, constitución, arquitectura, roadmap e invariantes.
- Demo de conversación y ensayo de parlamento con datos sintéticos.

Se excluyeron Git e historial, bases de datos, perfiles privados, recuerdos,
conversaciones, credenciales, entornos, modelos, logs, capturas, informes de la
máquina y resultados de campañas. Se omitieron los cuatro runners de evaluación
delegada y sus tests porque dependen de baselines históricos que no se distribuyen.
Las referencias personales y las fixtures de salud fueron sustituidas en esta copia.

El chat web conserva su frontera privada: no viene publicado ni con autenticación
de producción. No se habilitó acceso anónimo ni se expuso un servidor. El código
de voz y Pet es opcional; sus dependencias y dispositivos no son necesarios para
las demos. La visión y el roadmap describen también objetivos futuros, no garantías
de capacidades ya disponibles.

Usá esta muestra desde el checkout con instalación editable: los perfiles y los
invariantes son archivos del proyecto. No se entrega como wheel autónomo.

Ver [PRIVACIDAD.md](PRIVACIDAD.md) y [VERIFICACION.md](VERIFICACION.md).
