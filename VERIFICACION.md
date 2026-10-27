# Verificación de la muestra — 2026-10-27

- Instalación editable desde cero en un entorno temporal, Python 3.14.7,
  con extras `test,web`: correcta.
- Suite Python: **949 passed, 4 skipped, 43 subtests passed**.
  Las omisiones corresponden a dos casos exclusivos de Windows y dos
  reproducciones específicas de CPython 3.11/3.12.
- Interfaz JavaScript: **23 tests aprobados**, sin fallos.
- `demo.py`: turno real completado con provider fijo, sesión temporal,
  memoria desactivada y almacenamiento temporal explícito.
- `python -m cli.parliament_cli --mode demo`: estado `completed`.
- Demo ejecutada desde un directorio ajeno al checkout: correcta.
- Entrypoint editable `miku --help`: correcto.
- Revisión de archivos y texto: sin identificadores personales conocidos,
  correos, rutas de usuario, formatos reconocibles de claves ni archivos
  privados excluidos. No hay Git, bases de datos ni entorno virtual.

No se verificaron conversación con un modelo real, voz, Pet visible,
Windows, despliegue web ni autenticación de producción. Las pruebas HTTP y
de JavaScript no sustituyen una comprobación visual en navegador.

Los documentos canónicos se conservan; esta distribución no ratifica cambios
de arquitectura. El perfil, la demo, el README y esta documentación son
material operativo propio de la muestra. No se publica ningún servicio.
