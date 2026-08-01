# Despliegue en DigitalOcean

La imagen no contiene credenciales. Todas las claves se suministran en tiempo de ejecución como variables de entorno.

## Opción recomendada: App Platform

App Platform construye el `Dockerfile`, publica HTTPS automáticamente y reinicia la API si falla.

1. Sube el proyecto a un repositorio privado de GitHub. Confirma antes que `.env` no esté incluido.
2. En DigitalOcean, crea una aplicación desde **App Platform > Create App** y conecta el repositorio.
3. Selecciona `Dockerfile` como método de construcción y configura el puerto HTTP `8080`.
4. Añade las variables de `.env.example`. Marca como **Encrypted/Secret** al menos:
   `OPENAI_API_KEY`, `VTEX_APP_KEY`, `VTEX_APP_TOKEN`,
   `COOLBOX_WEB_API_USERNAME` y `COOLBOX_WEB_API_PASSWORD`.
5. Configura la ruta de health check como `/health` y despliega.
6. Prueba la URL asignada por DigitalOcean:

   ```bash
   curl -fsS https://TU-APP.ondigitalocean.app/health
   ```

El archivo `deploy/digitalocean-app.yaml.example` contiene la misma configuración como plantilla declarativa. Sustituye el repositorio y carga los valores secretos desde el panel; no confirmes secretos reales en ese archivo.

## Alternativa: Droplet con Docker Compose

Usa una Droplet Ubuntu con Docker instalado y permite TCP 8080 en el firewall solo durante la prueba inicial.

```bash
git clone URL_DE_TU_REPOSITORIO chatbot-vtex
cd chatbot-vtex
cp .env.example .env
nano .env
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/health
docker compose logs --tail=100 chatbot-api
```

Para actualizar:

```bash
git pull --ff-only
docker compose up -d --build
```

En producción, coloca un proxy HTTPS (por ejemplo Caddy o Nginx) delante del puerto 8080 y después retira el acceso público directo a ese puerto mediante DigitalOcean Cloud Firewall. App Platform ya resuelve HTTPS y el enrutamiento, por eso es la opción más sencilla.

## Prueba funcional

El endpoint conserva el contrato del chatbot:

```bash
curl -fsS https://TU_DOMINIO/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"mensaje":"¿Qué tiendas tienen stock del iPhone 15?","historial":[]}'
```

No escribas tokens en el comando ni en el payload; la API los obtiene exclusivamente de sus variables de entorno.
