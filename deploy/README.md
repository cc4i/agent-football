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

**`maxScale: "1"` is a target, not a promise.** A second container was found up
under this exact yaml, hours after the deploy that started it, taking no
traffic but its own health probe. Filter Cloud Logging by `labels.instanceId`
to see it. It runs the watchdog like any other arena, so it decides about half
the abandonments in the venue and publishes them to a bus with nobody on it.
The arena is built to survive that overlap now - liveness is a column, and the
sweep re-tells its own sockets what the database says - but anything you add
that assumes one process is assuming something Cloud Run has not agreed to.

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
    cloudbuild.googleapis.com \
    compute.googleapis.com \
    servicenetworking.googleapis.com
```

`cloudbuild` is in the list because the images are built there rather than on
the laptop. See "Building somewhere else" below for why. `compute` and
`servicenetworking` are there because the database has no public IP: the first
allocates the range it lives in and the second peers that range to your
network.

### The image repository

```bash
gcloud artifacts repositories create futsal \
    --repository-format=docker --location="$REGION"
```

`futsal` is the name `service.yaml` and `cloudbuild.yaml` both spell out.

### The private connection

The VPC and its subnet are assumed to exist. What does not exist yet is the
block the managed instance lives in:

```bash
export NETWORK=default    # the VPC the database and the service share
export SUBNET=default     # in $REGION, where the service gets its interface

gcloud compute addresses create "google-managed-services-${NETWORK}" \
    --global --purpose=VPC_PEERING --prefix-length=16 \
    --network="projects/${PROJECT}/global/networks/${NETWORK}"

gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges="google-managed-services-${NETWORK}" \
    --network="$NETWORK" --project="$PROJECT"
```

A Cloud SQL instance with a private address does not sit in your network. It
sits in Google's, and its address comes out of a range you allocate in yours
and then hand to service networking, which peers the two. That is what private
services access is, and it is the reason the range is created before the
instance rather than after.

There is one such connection per network and every managed service shares it,
so check before adding a second range:

```bash
gcloud services vpc-peerings list --network="$NETWORK" --project="$PROJECT"
```

If that already answers, both commands above are already done. Deleting the
connection later cuts private connectivity to everything using it, not only to
this instance.

The subnet has to be in `$REGION` and no smaller than a `/26`: Direct VPC
egress puts the service's own interface in it, and Cloud Run will not admit a
revision naming a subnet in another region.

### The database

```bash
gcloud sql instances create arena-pg \
    --database-version=POSTGRES_18 \
    --region="$REGION" \
    --tier=db-g1-small \
    --edition=enterprise \
    --network="projects/${PROJECT}/global/networks/${NETWORK}" \
    --no-assign-ip

gcloud sql databases create arena --instance=arena-pg

DB_PASSWORD="$(openssl rand -base64 24)"
gcloud sql users create arena --instance=arena-pg --password="$DB_PASSWORD"
```

`db-g1-small` holds a workshop's leaderboard without noticing. A venue of fifty
phones is still one connection from one instance, so the tier is about how long
you want to keep the history rather than about the load.

`--edition=enterprise` is load-bearing rather than decoration. Anything from
Postgres 16 up defaults to the Enterprise Plus edition, which only sells
dedicated-core machines, and the create fails with `Invalid Tier (db-g1-small)
for (ENTERPRISE_PLUS) Edition`. Shared cores live in the Enterprise edition
only; Postgres 18 runs happily there.

`--no-assign-ip` is the whole point of the section above it, and `--network` is
what makes it survivable: drop the second and the instance has no address at
all.

There is no `--ssl-mode` here and no `sslmode` in `ARENA_DB`, which is a
decision rather than an oversight: a private address is not an encrypted one,
and this connection is not encrypted. What is on the wire is the query traffic
- display names, masked emails, results - between two ends of one VPC. The
password is not among it, because Postgres 18 authenticates with SCRAM rather
than by sending it. Encrypting it is `--ssl-mode=ENCRYPTED_ONLY` on the
instance and `?sslmode=require` in `ARENA_DB`, together and not separately;
pinning the instance's CA and asking for `verify-ca` is the further step, for
the day the wire has to be trusted rather than merely private.

The arena reaches the instance by TCP on that private address, which is why
`service.yaml` carries `network-interfaces` and no `cloudsql-instances`. The
socket that annotation mounts is the Cloud SQL Auth Proxy and the proxy dials
the public address this instance does not have. `deploy.sh` reads the private
one off the instance on every deploy and renders it into `ARENA_DB`, so it is
written down nowhere and an instance rebuilt between workshops needs no edit.

What you give up is reaching the database from the laptop. A private address is
routable from inside the VPC and nowhere else, so `gcloud sql connect` and a
local `psql` both have nothing to dial. Run SQL from Cloud SQL Studio in the
console instead - it goes through the Admin API rather than the network, works
against a private-only instance, and needs `roles/cloudsql.studioUser`.

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
gcloud iam service-accounts create futsal-arena --display-name="The futsal arena"

SA="futsal-arena@${PROJECT}.iam.gserviceaccount.com"
for role in roles/secretmanager.secretAccessor \
            roles/aiplatform.user \
            roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:${SA}" --role="$role" --condition=None
done
```

`futsal-arena` and not `arena`, which is the name everything else here uses: an
account ID has to be at least six characters and `arena` is five, so the create
answers `The account ID "arena" does not have a length between 6 and 30`.
`service.yaml`'s `serviceAccountName` spells the longer name.

`--condition=None` is the difference between a loop that runs and a loop that
stops three times to ask a question. A project whose policy already holds one
conditional binding - Cloud Build's connection setup leaves one behind - makes
the flag mandatory: without it gcloud will not guess that you meant an
unconditional binding, and it prompts for the condition instead. Under
`deploy.sh` or any other script that is a hang rather than a prompt.

Three roles, one per thing the instance touches: the secrets it starts with,
Vertex for the chain, and its own logs. The database is not one of them.
`roles/cloudsql.client` is the Auth Proxy's permission - it authorises the
connector to open the tunnel - and a TCP connection to a private address with a
password in `PGPASSWORD` never asks the Cloud SQL API anything. Add it back the
day the socket comes back, and not before.

The interface into the VPC is the Cloud Run service agent's business rather
than this account's, and in a single project it already holds the role for it.
In a Shared VPC it needs `roles/compute.networkUser` in the host project.

## Deploying

```bash
export PROJECT=your-project-id
export REGION=europe-west1
export NETWORK=default        # both default to `default` if unset
export SUBNET=default
deploy/deploy.sh
```

It warns if the tree is dirty, asks before dropping the live matches, builds
the three images in Cloud Build, reads the database's private address, renders
`service.yaml` with your project, region, network, subnet, that address and the
short commit as the tag, replaces the service, and prints the URL.

It stops before the deploy if the address it read is a public one, because a
public address is the one thing that renders cleanly here and then cannot be
reached: `private-ranges-only` egress does not carry it, and the arena spends
its startup timing out against a route nothing in the log names.

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

## Rehearsing the load before the room does

`arena/tests/test_load_rehearsal.py` drives fifty rooms at ten frames a second
for a full three-minute match. It runs against a laptop by default and the
numbers it prints there are worth having, but three of the things it is meant
to check exist only up here:

- **TLS termination.** Fifty rooms is a hundred and fifty-one websockets
  through the front end rather than straight onto a socket, and the frames are
  `wss` rather than `ws`.
- **The 3600-second timeout, actually holding.** A three-minute match is the
  first thing that has run long enough on one socket to tell you the
  `timeoutSeconds` in `service.yaml` is the number in force. A default of 300
  looks identical until a match passes five minutes.
- **CPU that is not throttled between requests.** `cpu-throttling: false` is
  what lets the sweep and the bus run in the gaps. Throttled, the frames
  arrive in bursts behind each request and the latency is somebody else's
  scheduler.

```bash
cd arena
ARENA_LOAD=1 \
ARENA_LOAD_URL="$(gcloud run services describe arena --region="$REGION" \
    --format='value(status.url)')" \
    uv run pytest tests/test_load_rehearsal.py -s
```

`-s` is not optional: the report is printed and pytest swallows the stdout of a
test that passed. Do it after "Letting the phones in", because every socket in
it is an anonymous one and a 403 looks like a venue that will not open. The
laptop still needs its own Postgres running: this is a pytest run and the
suite's fixtures make their throwaway database whether or not this particular
test looks at it.

Two things it cannot measure from out here, and says so in its own output: the
event-loop lag and the bus's drop counters are read from inside the process,
and the row counts come from a database that is not this laptop's to query. The
frame rate, the latency and the delivery counts are measured from this end and
are the same numbers either way.

It plays real matches. A hundred players named `Blue 0` through `Red 49`, fifty
finished rooms and a hundred results land in Cloud SQL and on the leaderboard.
Rehearse before the workshop rather than during it. The board is computed from
`result`, so clearing that clears the board:

```sql
DELETE FROM result WHERE player_id IN (
    SELECT id FROM player WHERE email_masked LIKE '%@rehearsal.example.com');
```

From Cloud SQL Studio in the console, not from here: the instance has no public
address, so the laptop has no route to it. That leaves the fifty rooms and their
event logs behind, which nothing renders and nothing reaps. They are a few
thousand rows and the tier will not notice, but they are there.

## If the first revision never goes ready

The coach and the captain bound `127.0.0.1` once, on the argument that the
containers of one Cloud Run instance share a network namespace - the same
argument `ARENA_COACH_URL=http://127.0.0.1:8000` rests on, and one that had been
reproduced under a `podman pod`. What that reproduction could not tell us is
where Cloud Run runs a `tcpSocket` startup probe from.

Revision `arena-00001` answered it: not from in there. The captain logged

```
INFO:     Uvicorn running on http://127.0.0.1:8001
ERROR:    STARTUP TCP probe failed 40 times consecutively for container
          "captain" on port 8001. Connection failed with status DEADLINE_EXCEEDED.
```

- the server up and listening, the probe against that same port timing out
every time, over five instance starts. The coach never ran to fail the same way
because it sits behind the captain in `container-dependencies`, so both were
widened together rather than one failed deploy apart.

So both sidecars bind `0.0.0.0` now, and a bind narrowed back to the loopback is
a revision that never goes ready. `arena/tests/test_images.py` holds that.

Widening published nothing. Neither server authenticates anything, and what
isolates them is that neither declares a port: Cloud Run routes external
requests to the ingress container alone, and Direct VPC egress is egress, so
nothing in the VPC can dial an instance either. The bind was never the lock.

The captain's bind is a second variable rather than a wider `CAPTAIN_HOST`.
`to_a2a` writes `CAPTAIN_HOST` into the agent card's `rpc_url`, and the coach
dials the url out of the card rather than the address it fetched the card from,
so that one stays `127.0.0.1` - an address to connect to - while `CAPTAIN_BIND`
is the wildcard to listen on. Collapsing them back into one breaks whichever end
loses.

If the one that fails is `arena`, the database is the first thing to rule out.
Its `/health` opens a connection, so an unreachable database reads as a startup
probe that never passes, and the log has psycopg waiting on a private address
rather than anything about networking. Three things make that address
unreachable and all three are in this file: the peering
(`gcloud services vpc-peerings list --network="$NETWORK"`), the subnet being in
`$REGION` and in the same network as the peering, and the instance still having
no public address for `deploy.sh` to have picked up instead.

The arena's `0.0.0.0` was never in question and stays: Cloud Run's container
contract requires the ingress container to listen on `0.0.0.0:$PORT`.

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
