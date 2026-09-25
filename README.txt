ARCHIVOS PARA GITHUB PAGES

1. index.html  -> visor principal; logos SEGEPLAN y CRS están embebidos.
2. data.json   -> archivo de datos que consume el visor.

Suba ambos archivos a la raíz del repositorio.
En GitHub: Settings > Pages > Deploy from a branch > main / root.

IMPORTANTE: cuando el pipeline de Kobo genere datos reales, debe actualizar/reemplazar data.json conservando la estructura esperada por el visor.
