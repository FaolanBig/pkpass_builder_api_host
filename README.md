# PKPass Builder API Host

A Dockerized HTTP API that creates Apple Wallet contact passes. Each pass displays
the submitted contact information and contains a QR code whose vCard payload can
be scanned to add the contact to a phone.

## Run

### Docker Compose

```sh
docker compose pull
docker compose up -d
```

Compose pulls `ghcr.io/faolanbig/pkpass-builder-api-host:latest` from GitHub
Container Registry. The image is published by the GitHub Actions workflow.
If the package is private, authenticate first with `docker login ghcr.io`.

The API is available at `http://localhost:8000`. Check its status and logs with:

```sh
docker compose ps
docker compose logs -f pkpass-builder
```

Stop the service with `docker compose down`. The default Compose configuration
uses unsigned development mode. To use signed mode, configure the signing
environment variables listed below and place the certificate, private key, and
WWDR certificate in `./certificates` (or set `CERTIFICATES_DIR` to another
directory). The mounted certificate directory is read-only.

### Docker

```sh
docker build -t pkpass-builder .
docker run --rm -p 8000:8000 pkpass-builder
```

By default, the service runs in unsigned development mode. Check
`http://localhost:8000/health` for service status and
`http://localhost:8000/docs` for the interactive API documentation. The
standalone OpenAPI specification is available in [openapi.json](openapi.json).

## Create a pass

Send a JSON request to `POST /passes`. Field names accept camelCase or snake_case;
the labels `Name`, `Academic Degree`, `Job Position`, and `Phone Number` are also
accepted.

```sh
curl -X POST http://localhost:8000/passes \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Ada Lovelace",
    "academicDegree": "PhD",
    "jobPosition": "Researcher",
    "email": "ada@example.org",
    "phoneNumber": "+1 555 0100",
    "department": "Computing",
    "location": "London"
  }' \
  -o contact.pkpass
```

The response is a `.pkpass` archive with `pass.json`, icons, a QR PNG, and the
Wallet QR barcode configuration. The QR payload is a vCard 3.0 containing the
submitted name, degree, position, email, phone, department, and location. The
pass front shows the name, position, degree, department, and location; the back
contains all fields.

## Signing modes

Set `PKPASS_SIGNING_MODE` to switch modes:

* `unsigned` (default): creates a package for development and inspection.
  Unsigned packages cannot be installed in Apple Wallet.
* `signed`: signs the package for Apple Wallet. Configure these environment
  variables and provide the files inside the container:

  * `PASS_TYPE_IDENTIFIER`: Pass Type ID registered with Apple.
  * `TEAM_IDENTIFIER`: Apple Developer Team ID.
  * `ORGANIZATION_NAME`: issuer name shown by Wallet.
  * `PASS_CERTIFICATE_PATH`: path to the Pass Type ID certificate in PEM format.
  * `PASS_PRIVATE_KEY_PATH`: path to its matching private key in PEM format.
  * `APPLE_WWDR_CERTIFICATE_PATH`: path to Apple's WWDR intermediate certificate.

Mount credentials read-only rather than copying them into the image. For example:

```sh
docker run --rm -p 8000:8000 \
  -e PKPASS_SIGNING_MODE=signed \
  -e PASS_TYPE_IDENTIFIER=pass.example.organization.contact \
  -e TEAM_IDENTIFIER=ABCDE12345 \
  -e ORGANIZATION_NAME='Example Organization' \
  -e PASS_CERTIFICATE_PATH=/run/secrets/pass-certificate.pem \
  -e PASS_PRIVATE_KEY_PATH=/run/secrets/pass-private-key.pem \
  -e APPLE_WWDR_CERTIFICATE_PATH=/run/secrets/wwdr.pem \
  -v "$PWD/certificates:/run/secrets:ro" \
  pkpass-builder
```

## Test

```sh
python -m unittest discover -s tests
```
