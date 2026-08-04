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
  "imagen": "https://coolboxpe.vteximg.com.br/.../iphone-15.jpg"
}
```

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
