# PKPass Builder API Host

A Dockerized HTTP API that creates Apple Wallet contact passes. Each pass displays
the submitted contact information and contains a QR code whose vCard payload can
be scanned to add the contact to a phone.

## Run

### Docker Compose

```sh
export API_KEY="$(openssl rand -hex 32)"
docker compose pull
docker compose up -d
```

Compose pulls `ghcr.io/faolanbig/pkpass-builder-api-host:v1.8.0-rc` from GitHub
Container Registry. The image is published by the GitHub Actions workflow.
Set `IMAGE_TAG` if you want to pin a specific published tag.
If the package is private, authenticate first with `docker login ghcr.io`.
The API key is required and is passed to the container through the `API_KEY`
environment variable. Keep it secret; do not commit it to the repository.

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
The unauthenticated `/health` endpoint is available for container health checks.

### Docker

```sh
docker build -t pkpass-builder .
docker run --rm -p 8000:8000 -e API_KEY="$API_KEY" pkpass-builder
```

By default, the service runs in unsigned development mode. Check
`http://localhost:8000/health` for service status and
`http://localhost:8000/docs` for the interactive API documentation. The
standalone OpenAPI specification is available in [openapi.json](openapi.json).

### Kubernetes

The [k8s.yaml](k8s.yaml) manifest creates the `pkpass-gen` namespace, deploys
the published container, and configures a Service and Traefik Ingress at
`pkpass-gen.brave-cobra.vcluster.k8s.vector.int`. The Service listens on port
`8443` for Traefik and forwards traffic to the container on port `8000`.

Create the namespace and API key Secret before applying the manifest:

```sh
kubectl create namespace pkpass-gen --dry-run=client -o yaml | kubectl apply -f -
export API_KEY="$(openssl rand -hex 32)"
kubectl create secret generic pkpass-gen-api \
  --namespace pkpass-gen \
  --from-literal=API_KEY="$API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
```

For signed Apple passes, prepare the Apple-issued Pass Type ID certificate and
its matching private key in PEM format, and obtain Apple's WWDR intermediate
certificate. The CSR used to request the certificate is not needed by the API.
Create the `pkpass-signing` Secret before applying `k8s.yaml` (or before
enabling signed mode on an existing deployment):

```sh
kubectl -n pkpass-gen create secret generic pkpass-signing \
  --from-file=pass-certificate.pem=/path/to/pass-certificate.pem \
  --from-file=pass-private-key.pem=/path/to/pass-private-key.pem \
  --from-file=wwdr.pem=/path/to/wwdr.pem \
  --dry-run=client -o yaml | kubectl apply -f -
```

Before deploying, replace `PASS_TYPE_IDENTIFIER`, `TEAM_IDENTIFIER`, and
`ORGANIZATION_NAME` in `k8s.yaml` with the values for your Apple Developer
account. The deployment mounts the Secret read-only at `/etc/pkpass-secrets` and uses
signed mode. Keep the private key out of source control; Kubernetes Secret data
is base64-encoded and should be protected using your cluster's secret-storage
and access-control practices.

After both Secrets exist and the Apple identifiers are set, apply the
deployment:

```sh
kubectl apply -f k8s.yaml
kubectl -n pkpass-gen rollout status deployment/pkpass-gen
```

The manifest pins the same `v1.8.0-rc` image used by Docker Compose. Update its
image tag when deploying a different published version. The default deployment
requires the signing Secret described above. Configure Kubernetes Secrets and
volumes separately if you need Google Wallet credentials.

## Workato custom connector

Copy [workato/connector.rb](workato/connector.rb) into a Workato Connector SDK
custom connector and create a connection with the API base URL (the HTTPS
ingress origin, without `/passes`) and the `API_KEY` configured on the service.
Keep the key in Workato's password field. The connector uses the `X-API-Key`
header for requests.

The connector enables Workato's on-prem agent (OPA) support. For a private
ingress, configure an OPA group and select it under **Connection type** when
creating the connection. The agent must be able to resolve and reach the
ingress and validate its TLS certificate. On-prem connectivity depends on your
Workato plan; see the [Workato OPA connector guide](https://docs.workato.com/en/developing-connectors/sdk/guides/authentication/on-prem).

The connection test calls the unauthenticated `/health` endpoint. A successful
test confirms reachability, **not** that the API key or signing configuration
is valid. Run a pass-creation action to verify those settings.

Both actions require all seven contact fields. **Create Apple Wallet pass**
returns `content_base64`, `filename`, and `content_type`; decode the Base64
content to binary when saving or attaching `contact.pkpass`. It does not return
a save URL. **Create Google Wallet save link** returns `saveUrl` and `objectId`;
it requires the Google Wallet server configuration below. The URL includes
contact information, so handle it as private data. API errors, including 401,
422, and 500 responses, fail the action rather than producing a pass.
Run the local connector contract tests with `ruby workato/test_connector.rb`.

## Configure colors

Set these service-wide environment variables to 6-digit hex colors (with or
without a leading `#`). The defaults preserve the existing palette:

* `SHOW_DEPARTMENT` (`false`): show the department on the pass and in the QR contact.
* `COMPANY_HOMEPAGE` (unset): company website shown on the pass and in the QR contact.
* `PKPASS_FOREGROUND_COLOR` (`#ffffff`): Apple Wallet pass text.
* `PKPASS_BACKGROUND_COLOR` (`#17324d`): Apple Wallet pass background.
* `PKPASS_LABEL_COLOR` (`#dce6f0`): Apple Wallet field labels.
* `GOOGLE_WALLET_BACKGROUND_COLOR` (`#17324d`): Google Wallet pass background.
* `ICON_BACKGROUND_COLOR` (`#17324d`) and `ICON_FOREGROUND_COLOR` (`#ffffff`):
  generated Apple pass icon.
* `QR_FOREGROUND_COLOR` (`#000000`) and `QR_BACKGROUND_COLOR` (`#ffffff`):
  generated QR image.

Configure them in the container environment or Compose `.env` file. The
Kubernetes deployment exposes the same variables in its container environment.
Invalid color values cause pass creation to fail with the variable name in the
error.

## Create a pass

Send a JSON request to `POST /passes` with the API key in the `X-API-Key` header.
Field names accept camelCase or snake_case; the labels `Name`, `Academic Degree`,
`Job Position`, and `Phone Number` are also accepted.
`jobPosition` is optional: omit it or set it to `null` to leave the position off
both wallet passes and their QR contacts. A supplied string must be nonempty.

```sh
curl -X POST http://localhost:8000/passes \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "givenName": "Ada",
    "surname": "Lovelace",
    "academicDegree": "PhD",
    "jobPosition": "Researcher",
    "email": "ada@example.org",
    "phoneNumber": "+1 555 0100",
    "department": "Computing",
    "street": "1 Main St",
    "postalCode": "SW1A 1AA",
    "city": "London",
    "country": "UK"
  }' \
  -o contact.pkpass
```

The response is a `.pkpass` archive with `pass.json`, icons, a QR PNG, and the
Wallet QR barcode configuration. The QR payload is a vCard 3.0 containing the
submitted given name and surname (prefixed with the academic degree unless it is `N/A`), position,
email, phone, and address, with the organization name as company. The pass front
shows the name, position, and the full address (street, postal code and city, country); the back contains all fields. The
department is only shown (pass and vCard) when `SHOW_DEPARTMENT` is `true`, and
`COMPANY_HOMEPAGE` (optional) adds the company website to the pass and vCard.
The address is sent as `street`, `postalCode`, `city`, and `country`. An optional profile picture can be sent in the Apple pass
request as `profilePicture`, containing the raw Base64-encoded bytes of a PNG,
JPEG, or WebP image (up to 5 MB, with neither dimension over 4096 pixels). The
service crops it to a square and packages Apple Wallet `thumbnail` images for
display on the pass front. For example, add
`"profilePicture": "<base64-encoded-image>"` to the JSON body. The
`profile_picture` snake_case spelling is also accepted. This field is ignored
by the Google Wallet endpoint.

An optional company logo can be included in every Apple pass by setting
`COMPANY_LOGO_PATH` to an image file available to the service.
For Docker Compose, put the image in the configured certificates directory
(default `./certificates`) and set, for example,
`COMPANY_LOGO_PATH=/run/secrets/company-logo.png`. The service packages
`logo.png`, `logo@2x.png`, and `logo@3x.png` variants; omit the setting to leave
the pass unchanged.

## Google Wallet

`POST /passes/google-wallet` returns a JSON response with a Google Wallet
`saveUrl` and the generated `objectId`. Send the same contact JSON body as for
the Apple endpoint and include the API key in the `X-API-Key` header. The
returned link lets the recipient add a Google Wallet Generic Pass; its QR code
contains the vCard contact data.

Before using this endpoint, set up a Google Wallet issuer, enable the Google
Wallet API, authorize a Google Cloud service account for the issuer, and create
an approved Generic Class. Configure:

* `GOOGLE_WALLET_ISSUER_ID`: your numeric Wallet issuer ID.
* `GOOGLE_WALLET_CLASS_ID`: the full ID of the approved Generic Class, in the
  form `<issuer-id>.<class-suffix>`.
* `GOOGLE_WALLET_SERVICE_ACCOUNT_JSON`: path in the container to the service
  account JSON key file. By default, Compose expects it at
  `/run/secrets/google-wallet-service-account.json`; mount it in the
  certificates/secrets directory and keep it out of source control.
* `GOOGLE_WALLET_ORGANIZATION_NAME`: optional pass title (defaults to
  `Contact Pass`).
* `GOOGLE_WALLET_ORIGINS`: optional comma-separated web origins where the
  save link will be used.
* `GOOGLE_WALLET_BACKGROUND_COLOR`: optional 6-digit hex background color
  (defaults to `#17324d`).

Google Wallet save links contain the pass data and must be kept private. Google
recommends keeping the signed JWT below 1,800 characters; the endpoint returns
an error if the generated JWT exceeds that limit.

Example:

```sh
curl -X POST http://localhost:8000/passes/google-wallet \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "givenName": "Ada",
    "surname": "Lovelace",
    "academicDegree": "PhD",
    "jobPosition": "Researcher",
    "email": "ada@example.org",
    "phoneNumber": "+1 555 0100",
    "department": "Computing",
    "street": "1 Main St",
    "postalCode": "SW1A 1AA",
    "city": "London",
    "country": "UK"
  }'
```

## Signing modes

Set `PKPASS_SIGNING_MODE` to switch modes:

* `unsigned` (default): creates a package for development and inspection.
  Unsigned packages cannot be installed in Apple Wallet. When identifiers are
  unset or empty, the package uses development placeholders for
  `passTypeIdentifier` and `teamIdentifier`; configure real Apple identifiers
  and signed mode before creating installable passes.
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
  -e API_KEY="$API_KEY" \
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
