MAPEO DE ACTORES DE COOPERACIÓN — SEGEPLAN CHIQUIMULA
Archivos para GitHub Pages

ARCHIVOS (todos van en la raíz del repositorio)

1. index.html                 -> visor (mapa de actores, gráficas, fichas, Excel y PDF). Logos SEGEPLAN y CRS embebidos.
2. data.json                  -> datos que consume el visor. Lo regenera la tarea automática.
3. actualizar_datos.py        -> descarga los envíos de Kobo y genera data.json.
4. catalogo_xlsform.json      -> opciones del formulario y catálogo de lugares poblados.
5. coordenadas_lugares.json   -> coordenadas de los lugares poblados (el campo "fuente" indica el origen).
6. .github/workflows/actualizar-cooperacion.yml -> tarea que ejecuta el script cada 6 horas.

PUBLICACIÓN
En GitHub: Settings > Pages > Deploy from a branch > main / root.

SINCRONIZACIÓN CON KOBO
La tarea necesita dos secretos en Settings > Secrets and variables > Actions:
  KOBO_TOKEN       token de la cuenta de Kobo
  KOBO_ASSET_UID   identificador del formulario
Para ejecutarla a mano: pestaña Actions > "Actualizar cooperación desde Kobo" > Run workflow.

CÓMO SE UBICAN LOS LUGARES
Kobo no guarda coordenadas: guarda el código del lugar poblado. El script busca ese código
en coordenadas_lugares.json. El script lee los dos modos del formulario:
  - comunidades registradas una por una (bloque cobertura_comunidad);
  - "varias comunidades a la vez" (bloque cantidades_grupo, código en g_lugar_i).
Si un lugar no tiene coordenadas en el archivo, el visor lo dibuja cerca del centro de su
municipio con borde punteado. Los lugares registrados como "otro, no aparece en la lista"
tampoco tienen coordenadas.

REVISAR DESPUÉS DE CADA EJECUCIÓN
El registro de la tarea avisa si algún programa indica trabajo comunitario pero llega sin
lugares poblados, y si se recuperaron lugares desde campos no previstos.

NOTA
Las descargas de Excel usan la librería SheetJS, que el visor carga desde internet al pulsar el botón.
