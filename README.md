# Chatbot VTEX/Coolbox en Python

Migración del servicio Spring Boot a Python 3.11+ con FastAPI. Conserva el contrato
`POST /api/chat`, el historial de conversación y las integraciones con VTEX y Web API
Coolbox/CIC.

## Qué mejora

- Operaciones HTTP asíncronas para atender varias conversaciones sin bloquear el servidor.
- Análisis de intención con salida estructurada y validada por Pydantic.
- Respuestas de precio, stock y soporte construidas de forma determinista para evitar inventar datos.
- Fallback local si OpenAI no responde.
- Reintentos acotados y timeouts para VTEX, Coolbox y OpenAI.
- Token de Coolbox compartido, protegido contra autenticaciones concurrentes y renovado cada 23 horas.
- Los DNI, pedidos, notas de crédito y ODS se detectan localmente y no se envían a OpenAI.
- Las fichas completas de catálogo y los resultados de soporte no se exponen en la respuesta pública.

## Configuración

En desarrollo, si no existe `.env`, el servicio reutiliza automáticamente las propiedades del
proyecto Java ubicado en:

```text
../chatbotvtex/src/main/resources/application.properties
```

Esto permite probar la migración sin duplicar secretos. Para producción, copia `.env.example` a
`.env` o configura las mismas variables en el entorno. `.env` está excluido de Git.

Variables principales:

```text
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_BASE_URL
VTEX_BASE_URL
VTEX_APP_KEY
VTEX_APP_TOKEN
COOLBOX_WEB_API_BASE_URL
COOLBOX_WEB_API_USERNAME
COOLBOX_WEB_API_PASSWORD
```

También puedes indicar otra ubicación del archivo Java con `LEGACY_PROPERTIES_PATH`.

Las consultas de pedidos por número usan VTEX OMS cuando `VTEX_APP_KEY` y
`VTEX_APP_TOKEN` están configurados. Las consultas por DNI/RUC/CEX y las órdenes
de servicio (ODS) continúan utilizando Web API Coolbox/CIC.

Las consultas de pedidos aceptan un número de pedido o un DNI/RUC/CEX. Las consultas de
devoluciones, extornos y ODS requieren la referencia correspondiente junto con el documento.
El chatbot puede reunir datos en mensajes consecutivos mediante el historial. Los mensajes
con identificadores de soporte, teléfonos o documentos no se envían a OpenAI.

El endpoint de chat limita por defecto cada IP a 30 solicitudes por minuto. Se puede
ajustar con `RATE_LIMIT_REQUESTS` y `RATE_LIMIT_WINDOW_SECONDS`. En producción también
se recomienda limitar tráfico en Nginx o en el firewall perimetral.

No guardes el `appToken` en el código, en `.env.example` ni en el repositorio.
Configúralo como secreto del entorno donde se despliegue el servicio.

## Instalación y ejecución

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python run.py
```

La API queda disponible en `http://localhost:8080`.

En este Mac, el `python3` del sistema puede pedir primero aceptar la licencia de Xcode:

```bash
sudo xcodebuild -license
```

Como alternativa, se puede ejecutar con Docker:

```bash
docker build -t chatbot-vtex-python .
docker run --env-file .env -p 8080:8080 chatbot-vtex-python
```

## Endpoint

### `POST /api/chat`

El request es compatible con el proyecto Java:

```json
{
  "mensaje": "¿Tiene stock?",
  "historial": [
    {
      "rol": "usuario",
      "contenido": "Me interesa la Laptop HP 15-fd0280la"
    },
    {
      "rol": "asistente",
      "contenido": "La Laptop HP 15-fd0280la cuesta S/ 3.489,00."
    }
  ],
  "ubicacion": {
    "latitud": -12.12,
    "longitud": -77.03
  }
}
```

Respuesta:

```json
{
  "respuesta": "📦 Disponibilidad general del catálogo: ...",
  "intencion": "CONSULTAR_STOCK",
  "imagen": "https://coolboxpe.vteximg.com.br/.../iphone-15.jpg",
  "esTransferencia": false,
  "esDespedida": false
}
```

`esTransferencia` cambia a `true` cuando el cliente solicita hablar con una persona o acepta
una transferencia previamente ofrecida. `esDespedida` cambia a `true` cuando el cliente cierra
la conversación. Ambos campos siempre se incluyen y su valor predeterminado es `false`.

Cuando el cliente consulta características de un producto, Coolbot responde la consulta y ofrece
asesoría humana. La transferencia solo se activa cuando el cliente la solicita o confirma.

`imagen` solo aparece cuando se consulta un producto concreto. Se envía una única imagen y
no vuelve a incluirse si el producto ya apareció en una respuesta previa del historial. En
consultas generales, de soporte o seguimientos del mismo producto, la respuesta conserva
únicamente `respuesta` e `intencion`.

El historial es opcional y acepta los roles `usuario`/`asistente` o `user`/`assistant`.
El widget puede enviar la conversación completa; la API conserva automáticamente los 10
mensajes más recientes para mantener el contexto sin rechazar conversaciones largas.

`ubicacion` también es opcional. Se requiere para responder qué tiendas cercanas
tienen el producto disponible para recojo. VTEX confirma la opción de recojo, pero
no expone el número exacto de unidades mediante este flujo.

Prueba rápida:

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"mensaje":"¿Cuánto cuesta un iPhone 15?","historial":[]}'
```

Salud y verificación de configuración, sin mostrar secretos:

```bash
curl http://localhost:8080/health
```

## Pruebas

```bash
pytest
ruff check .
```

Las pruebas unitarias no consumen VTEX, Coolbox ni OpenAI.

## Chatbot Sodimac Perú

El nuevo `POST /api/chatsodimac` funciona de forma independiente de `POST /api/chat`.
Acepta `mensaje`, `historial` opcional y el mismo formato de respuesta:
`respuesta`, `intencion`, `imagen` opcional, `esTransferencia` y `esDespedida`.
Conserva los últimos 10 mensajes. En este endpoint cada mensaje del historial admite
hasta 20.000 caracteres para poder reenviar extractos completos del PDF.

```bash
curl -X POST http://localhost:8080/api/chatsodimac \
  -H 'Content-Type: application/json' \
  -d '{"mensaje":"Busco un taladro Bosch","historial":[]}'
```

- Catálogo: consulta HTML público únicamente en `https://www.sodimac.com.pe/sodimac-pe/`.
  Extrae datos estructurados de las páginas de búsqueda y fichas de producto, sin
  ejecutar JavaScript ni seguir redirecciones a otros dominios. Los enlaces de imagen
  publicados por la página se devuelven desde `media.sodimac.com.pe`; no se descargan.
- Precios: distingue internet, oferta, regular y tarjeta CMR; conserva la indicación de
  precio tachado. No calcula descuentos ni inventa precios ausentes. Incluye enlace de
  fuente y vendedor, ya que la página también publica productos de terceros.
- Stock: este flujo no verifica inventario por tienda ni cantidad de unidades. La
  ubicación es aceptada por compatibilidad, pero no se usa para calcular stock o envío.
- Productos: ofrece hablar con un asesor y activa `esTransferencia` cuando el cliente
  lo solicita o acepta la oferta del último mensaje del asistente. La aplicación que
  consume la API debe ejecutar la transferencia real; el backend devuelve la señal.
- Documento: se procesa desde `documents/sodimac/ces.pdf` mediante
  `python -m app.sodimac_index`. Extrae el texto de cada versión y usa OCR en páginas
  con imágenes o sin texto seleccionable. Guarda embeddings multilingües locales,
  fragmentos y metadatos en Qdrant. La intención es `CONSULTAR_DOCUMENTO`;
  las páginas se conservan internamente y no aparecen en las respuestas.
  La búsqueda combina vectores densos y palabras clave con fusión RRF.
  Si no hay una versión activada o Qdrant no está disponible, se informa el problema;
  no se responde desde una copia antigua incluida en el código.
- IA: reutiliza `OPENAI_API_KEY`, `OPENAI_MODEL` y `OPENAI_BASE_URL`.
  Para consultas CES primero recupera hasta 3 fragmentos de Qdrant y después realiza
  una llamada dedicada a OpenAI para redactar hasta 4 puntos breves para WhatsApp.
  Cada punto identifica su fuente recuperada y sus cifras se validan contra ella.
  Envía hasta 6 mensajes previos, limitados a 1.200 caracteres cada uno; conserva los
  resúmenes breves para continuar el tema y evitar repeticiones. No reenvía respuestas
  antiguas extensas del PDF. El consumidor debe enviar `historial` en cada petición;
  el servidor no almacena conversaciones. Si la redacción falla o falta la API key,
  informa la indisponibilidad sin mostrar texto crudo del PDF. No usa búsqueda web
  de OpenAI. Productos mantienen sus datos verificados desde Sodimac y la consulta
  de segmento PostgreSQL no utiliza IA.
- Docker: Compose incorpora Qdrant y un proceso de indexación bajo el perfil `tools`.
  Los datos, el modelo y los informes usan volúmenes persistentes independientes.
  Qdrant no publica puertos en el servidor. El modelo se descarga la primera vez;
  posteriormente genera embeddings locales sin pagar tokens a un proveedor.
  Requiere salida HTTPS para esa descarga, el catálogo y la IA configurada.
- Límites: los dos endpoints comparten el límite por IP configurado. Si Sodimac
  bloquea el acceso o cambia su estructura HTML, se informa que no se pudo verificar
  la información; no se sustituye por resultados de otras webs ni datos inventados.

Ejemplo de consulta documental: `{"mensaje":"¿Qué descuentos hay en alquiler de herramientas para socios CES?","historial":[]}`.
La guía [Ubuntu + Qdrant](deploy/README_SODIMAC_UBUNTU.md) incluye la instalación,
la carga inicial y la actualización segura del PDF sin reconstruir el chatbot.

Validación específica (sin consumir servicios externos):

```bash
pytest tests/test_sodimac.py tests/test_sodimac_vectors.py
```
