# Sodimac: PDF actualizable y Qdrant en Ubuntu

Este despliegue usa el proyecto en `/root/chatbotvtexphyton`. Las rutas de Compose
son relativas al proyecto; no hay rutas de macOS ni rutas absolutas del host en la imagen.
Requiere Docker Engine y Docker Compose v2 ya instalados.

## Servicios y almacenamiento

- `chatbot-api`: API existente, con `POST /api/chat` y `POST /api/chatsodimac`.
- `qdrant`: BD vectorial, accesible solo dentro de la red de Compose en `http://qdrant:6333`.
- `sodimac-index`: comando de administración, ejecutado con `docker compose run --rm`.
- `documents/sodimac/ces.pdf`: PDF de entrada, montado de solo lectura en `/documents`.
- `qdrant-data`: datos y versiones del documento.
- `embedding-models`: caché compartida del modelo multilingüe y bloqueo de indexación.
- `document-reports`: informes de extracción para revisar OCR y tablas.

Se utiliza `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` con FastEmbed
(768 dimensiones). Los embeddings se generan en CPU. La primera indexación descarga
el modelo; no requiere una cuenta ni una clave de una API de embeddings. El modelo y
los datos persisten al reiniciar contenedores. No ejecutes `docker compose down -v`
si quieres conservar esos volúmenes.

## Primera instalación

Después de subir estos cambios a GitHub, en el servidor:

```bash
cd /root/chatbotvtexphyton
git pull --ff-only
```

Si todavía no existe `.env`, créalo a partir de `.env.example` y configura las
credenciales de las integraciones. Conserva el `.env` existente cuando actualices.
Compose añade las variables internas de Qdrant; no necesitas agregar su URL al `.env`.
`QDRANT_API_KEY` es opcional mientras la BD permanezca en esta red privada y no se
publique su puerto; si la configuras en `.env`, Compose aplica la misma clave a los
servicios. El puerto HTTP existente se controla con `HOST_PORT`.

```bash
mkdir -p documents/sodimac
docker compose up -d --build
```

El repositorio incluye el PDF proporcionado como `documents/sodimac/ces.pdf`.
Puedes sustituirlo por el PDF definitivo antes de indexar. Colócalo con ese nombre
y asegúrate de que los contenedores puedan leerlo:

```bash
chmod 755 documents documents/sodimac
chmod 644 documents/sodimac/ces.pdf
docker compose run --rm sodimac-index index /documents/ces.pdf
```

El comando devuelve JSON con `collection`, número de fragmentos, páginas con OCR y
ubicación del informe. **No activa todavía el documento nuevo.** Copia el valor
completo de `collection` para los siguientes comandos:

```bash
SODIMAC_VERSION='sodimac_ces_REEMPLAZAR_POR_EL_IDENTIFICADOR_DEVUELTO'
mkdir -p documents/sodimac/reports
docker compose run --rm sodimac-index report "$SODIMAC_VERSION" > documents/sodimac/reports/revision.json
less documents/sodimac/reports/revision.json
```

Revisa especialmente montos, porcentajes, categorías y tablas de las páginas
marcadas `ocr: true`. El PDF suministrado incluye una tabla de categorías como imagen.
OCR usa Tesseract con español e inglés; la confianza media debe superar el umbral
mínimo, pero no garantiza exactitud de cada número. El informe permite comprobar
la extracción antes de habilitarla. Si un documento ya contiene OCR incorrecto o
una tabla vectorial sin texto seleccionable, fuerza el reconocimiento de todas las
páginas con `index /documents/ces.pdf --ocr always` y revisa el nuevo informe.

Tras revisar el informe, activa esa versión:

```bash
docker compose run --rm sodimac-index activate "$SODIMAC_VERSION"
docker compose run --rm sodimac-index status
curl -fsS http://127.0.0.1:8080/api/chatsodimac \
  -H 'Content-Type: application/json' \
  -d '{"mensaje":"¿Cómo puedo inscribirme al CES?","historial":[]}'
```

Usa tu puerto configurado si `HOST_PORT` es distinto de 8080. No hace falta reiniciar
la API: cada consulta resuelve la versión activa. Las referencias de página no se
muestran al cliente.

## Actualizar el PDF

1. Sustituye `/root/chatbotvtexphyton/documents/sodimac/ces.pdf` por el archivo nuevo.
2. Ejecuta `docker compose run --rm sodimac-index index /documents/ces.pdf`.
3. Revisa su informe con `report` y activa su `collection` con `activate`.

No necesitas modificar código, reconstruir la imagen ni generar embeddings de nuevo
al reiniciar. La versión se identifica por el hash del PDF, modelo y configuración
del proceso. Cargar de nuevo el mismo archivo/configuración reutiliza el índice.
Si falla la extracción, el OCR, los embeddings o la subida, la versión anterior
permanece activa. El cambio del alias se hace atómicamente al activar la versión.

Las versiones antiguas se conservan para permitir volver atrás:

```bash
docker compose run --rm sodimac-index activate 'sodimac_ces_IDENTIFICADOR_ANTERIOR'
```

No se eliminan automáticamente; las actualizaciones consumen almacenamiento adicional.
Mantén la misma configuración/modelo en API e indexador. Cambiar el modelo requiere
reindexar; la aplicación rechaza un índice de un modelo diferente.

## Diagnóstico

```bash
docker compose ps
docker compose logs --tail=100 qdrant chatbot-api
docker compose run --rm sodimac-index status
```

Antes de la primera activación el catálogo sigue funcionando, pero las consultas del
PDF informan que no se pudo consultar el documento. El health check `/health` confirma
que la API está viva; no garantiza que exista un documento activado.

El proceso valida tamaño (50 MB), páginas (1 a 300), texto extraído y completitud del
índice. Si el OCR falla, corrige el documento o revisa la extracción antes de activar.
La búsqueda recupera hasta 3 secciones y limita el contexto a 6.500 caracteres; combina
similitud semántica con términos exactos. Se deben verificar preguntas reales y ajustar
`SODIMAC_MIN_VECTOR_SCORE` si es necesario: ningún buscador garantiza recuperar siempre
la sección correcta. La IA mantiene instrucciones de usar solo las fuentes recuperadas.

## Referencias técnicas

- [Qdrant en Docker](https://qdrant.tech/documentation/quickstart/)
- [Cambio atómico de alias](https://qdrant.tech/documentation/manage-data/collections/)
- [Búsqueda híbrida](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Modelos de FastEmbed](https://qdrant.github.io/fastembed/examples/Supported_Models/)

### Descarga del modelo en contenedores de solo lectura

Compose dirige tanto FastEmbed como las cachés auxiliares de Hugging Face/Xet
al volumen `/models`. `FASTEMBED_CACHE_PATH` por sí solo no cambia la caché de Xet.
Si una instalación anterior falla intentando escribir en `/home/chatbot/.cache`,
actualiza el repositorio, recrea la API y repite la indexación:

```bash
git pull --ff-only
docker compose up -d chatbot-api
docker compose run --rm sodimac-index index /documents/ces.pdf
```

Este cambio de configuración no requiere reconstruir la imagen ni borrar volúmenes.
El aviso de Hugging Face sobre solicitudes sin autenticación no exige un token para
este modelo público.
