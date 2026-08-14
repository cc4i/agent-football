# Deploying the arena

One Cloud Run service, `arena`, with three containers in it: the arena on the
port, and the coach and the captain beside it on the loopback interface the
three share. One Cloud SQL instance behind it. The dugout is not here and never
will be - it embeds the Antigravity CLI and runs shell commands unrestricted,
so it stays on the presenter's laptop.

`service.yaml` is the whole topology. Everything below either creates something
it refers to or renders and applies it.

## Read this first

**Every deploy drops every live match.** `maxScale: "1"` is a correctness
constraint - the match bus, host liveness and the chain's semaphore are all in
one process - so there is no second instance to hand over to and no rolling
update to be had. The new revision starts, the old one stops, and every phone
in the venue loses its socket mid-match. A crash does exactly the same thing.

The database survives both. Players, rooms, the event log and the leaderboard
are all in Cloud SQL, so a manager who reloads gets their history back. What
they do not get back is the match they were playing. Deploy between matches.

## What it costs to leave up

`minScale: "1"` with `cpu-throttling: false` bills for the whole lifetime of
the service rather than per request: 4 vCPU and 8 GiB of the arena's container,
plus the coach's and the captain's 2 and 4 each, charged continuously whether
anybody is playing or not. The Cloud SQL instance never stops either. This is
not a scale-to-zero service and cannot be made into one without giving up the
single instance the correctness rests on.

Delete the service and stop the SQL instance between workshops:

```bash
gcloud run services delete arena --region="$REGION"
gcloud sql instances patch arena-pg --activation-policy=NEVER
```

## Once per project

```bash
export PROJECT=your-project-id
export REGION=europe-west1        # Run, Cloud SQL and Artifact Registry
gcloud config set project "$PROJECT"
```

Vertex is reached at `global` rather than in `$REGION`; that is set in
`service.yaml` and is deliberate, because the models the chain uses are not in
every region.

### The APIs

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com
```

`cloudbuild` is in the list because the images are built there rather than on
the laptop. See "Building somewhere else" below for why.

### The image repository

```bash
gcloud artifacts repositories create futsal \
    --repository-format=docker --location="$REGION"
```

`futsal` is the name `service.yaml` and `cloudbuild.yaml` both spell out.

### The database

```bash
gcloud sql instances create arena-pg \
    --database-version=POSTGRES_18 \
    --region="$REGION" \
    --tier=db-g1-small

gcloud sql databases create arena --instance=arena-pg

DB_PASSWORD="$(openssl rand -base64 24)"
gcloud sql users create arena --instance=arena-pg --password="$DB_PASSWORD"
```

`db-g1-small` holds a workshop's leaderboard without noticing. A venue of fifty
phones is still one connection from one instance, so the tier is about how long
you want to keep the history rather than about the load.

The arena reaches it over the Unix socket the `cloudsql-instances` annotation
mounts, which is why `ARENA_DB` in `service.yaml` names
`/cloudsql/PROJECT:REGION:arena-pg` as its host and no port at all.

### The four secrets

```bash
for name in arena-db-password arena-secret arena-email-salt arena-service-token; do
    gcloud secrets create "$name" --replication-policy=automatic
done

printf '%s' "$DB_PASSWORD"            | gcloud secrets versions add arena-db-password    --data-file=-
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets versions add arena-secret         --data-file=-
printf '%s' "$(openssl rand -hex 16)" | gcloud secrets versions add arena-email-salt     --data-file=-
printf '%s' "$(openssl rand -hex 24)" | gcloud secrets versions add arena-service-token  --data-file=-
```

`printf` rather than `echo`, and no `--data-file` pointing at a file somebody
typed into. A trailing newline is part of the secret: an `ARENA_SERVICE_TOKEN`
with one on the end is a token the specialists send and the arena never
matches, and the symptom is a shout that reports a huddle over a squad that
never moved.

`arena-email-salt` cannot be rotated afterwards without turning every returning
player into a stranger. Set it once.

### The service account

```bash
gcloud iam service-accounts create arena --display-name="The futsal arena"

SA="arena@${PROJECT}.iam.gserviceaccount.com"
for role in roles/cloudsql.client \
            roles/secretmanager.secretAccessor \
            roles/aiplatform.user \
            roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:${SA}" --role="$role"
done
```

Four roles, one per thing the instance touches: the database socket, the
secrets it starts with, Vertex for the chain, and its own logs.

## Deploying

```bash
export PROJECT=your-project-id
export REGION=europe-west1
deploy/deploy.sh
```

It warns if the tree is dirty, asks before dropping the live matches, builds
the three images in Cloud Build, renders `service.yaml` with your project,
region and the short commit as the tag, replaces the service, and prints the
URL.

### Letting the phones in

`gcloud run services replace` never touches the invoker policy, so this is a
separate command and a one-off:

```bash
gcloud run services add-iam-policy-binding arena \
    --region="$REGION" --member=allUsers --role=roles/run.invoker
```

Without it a scanned QR code lands on a 403 and nothing on the phone explains
why.

### The last two steps of a first deploy

**Set the public URL.** The first deploy needs no `ARENA_PUBLIC_URL`: the arena
works the address out per request from the forwarded scheme and the Host
header, which is what lets a service that has no name yet still print a QR code
that works. It says so on the way up, once per start:

```
ARENA_PUBLIC_URL unset; the public URL is being worked out per request and
should be set explicitly now that the service has a name
```

That warning in the log is how you know it is unset. Now that `deploy.sh` has
printed the `*.run.app` URL, add it to the arena container's `env:` in
`service.yaml` and deploy again:

```yaml
            - name: ARENA_PUBLIC_URL
              value: https://arena-xxxxxxxxxx.europe-west1.run.app
```

**Shout once.** The coach's startup probe is a `tcpSocket` on 8000 and that is
all it can honestly be: `adk web` binds its port and serves with no credentials
at all, and the ADK resolves them lazily at the first model call. A missing
`aiplatform` binding, a wrong project or a model that is not available in
`global` all pass the probe and surface as a shout that fails partway down the
chain. So open the deployed arena, take a seat, kick off and shout once. It is
the only check that covers the half of the service the probes cannot see.

## If the first revision never goes ready

The coach and the captain bind `127.0.0.1`, on the argument that the containers
of one Cloud Run instance share a network namespace - the same argument
`ARENA_COACH_URL=http://127.0.0.1:8000` rests on, and one that has been
reproduced under a `podman pod`. What that reproduction cannot tell us is where
Cloud Run runs a `tcpSocket` startup probe from.

If the revision fails with a startup-probe timeout on `coach` or `captain`,
test that first: the probe may be dialling from outside the instance, where a
loopback bind is not reachable. Widen that one image's bind to `0.0.0.0`
(`game/Dockerfile.coach`'s `--host`, or `CAPTAIN_HOST` in
`game/Dockerfile.captain`), redeploy, and keep the deploy log as the evidence
for which way it went.

Widen only the one that failed, and do not pre-empt it. The arena's `0.0.0.0`
is a different case and stays: Cloud Run's container contract requires the
ingress container to listen on `0.0.0.0:$PORT`.

## Building the images here instead

The deploy builds in Cloud Build. It is amd64 natively, which this Mac is not,
and it already holds the credentials for the push. A local build is a debugging
aid rather than a deploy path:

```bash
podman machine start
podman build --platform linux/amd64 -f arena/Dockerfile          -t arena:local   .
podman build --platform linux/amd64 -f game/Dockerfile.coach     -t coach:local   .
podman build --platform linux/amd64 -f game/Dockerfile.captain   -t captain:local .
```

`--platform linux/amd64` because Cloud Run will not run the arm64 image an
Apple Silicon Mac builds by default. It costs an emulated `npm ci` and three
emulated `uv sync`s, which is the other reason the real build is not here.

Running the arena image by hand, against this machine's Postgres:

```bash
podman run --rm -p 8080:8080 \
  -e ARENA_DB="postgresql://$(whoami)@host.containers.internal:5432/arena" \
  -e ARENA_SECRET=dummy-session-secret \
  -e ARENA_EMAIL_SALT=dummy-salt \
  -e ARENA_SERVICE_TOKEN=dummy-token \
  arena:local

curl -s localhost:8080/health          # {"ok":true,"service":"arena"}
```

`curl -s` and not `curl -I`: `/health` is a FastAPI route and GET-only, so a
HEAD against a perfectly healthy arena answers 405.

The image bakes in `ARENA_ENV=production`, so it refuses to start without those
three. That refusal is the image working.

## Watching it

```bash
gcloud run services describe arena --region="$REGION"
gcloud run services logs read arena --region="$REGION"
gcloud beta run services logs tail arena --region="$REGION"
gcloud run revisions list --service=arena --region="$REGION"
```

`tail` is `beta` and only `beta`: the GA group has `read` alone, and
`gcloud run services logs tail` answers `Invalid choice: 'tail'`.

Logs from all three containers arrive interleaved and each line carries the
container name, which is the quickest way to tell an arena that refused a patch
from a specialist that never sent one.
