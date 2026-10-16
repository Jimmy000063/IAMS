# Connecting an attendance device

For whoever is setting up a biometric reader, turnstile or punch-log export.

ASCII only, deliberately: these files were corrupted once by a shell
re-encoding them.

---

## Read this first

**No specific machine is supported by name.** This system has no code written
for any particular biometric or access-control product, and no physical device
has ever been tested against it. What exists is three general ways for punches
to arrive, and a configuration screen for describing what your machine sends.

That is not a gap waiting to be filled in silence - it is the honest position
until someone obtains a device's protocol documentation and tests against a real
unit.

**Attendance works without any device.** Employees punch in from the browser.
Adding a device gives you a second source of punches. If the device breaks,
loses its network, or is never bought, attendance keeps working.

---

## 1. Which option fits your machine

Check the machine's manual or its web interface for any of the following, in
this order of preference.

### It can POST to a URL (a "webhook", "HTTP push", "real-time upload")

Use the **webhook** adapter. This is the best option: punches arrive within
seconds and nothing has to be scheduled.

Status: implemented and tested against its contract.

### It can export a punch log to CSV or Excel

Use the **CSV upload** adapter. Almost every attendance machine can do this. It
needs no network connection to the device at all - you download the export and
upload the file here.

Status: implemented and tested against its contract.

### It exposes an HTTP endpoint returning JSON

Use the **REST polling** adapter. The system asks the device for recent punches
on a schedule.

Status: **generic**. The transport is implemented and tested against a stub, but
it has never been run against physical hardware. Verify it against your unit
before relying on it for payroll.

### None of the above

Then this system cannot read your device yet, and nobody should tell you
otherwise. Two routes forward:

1. Check whether the vendor supplies middleware that can do any of the three
   things above. Many do.
2. Send us the device's protocol or API documentation and arrange access to a
   unit for testing. An adapter can then be written and tested properly.

Meanwhile, web punching covers attendance, and the CSV route covers any machine
that can produce an export.

---

## 2. What you need before you start

- The device's identifier for each employee - its **enrolment number**, not
  their employee code. Usually visible in the device's user list.
- One sample of what the device sends: an exported CSV row, or a copy of the
  JSON it posts. This is what the field mapping is written against.
- **How the device writes dates.** This matters more than it looks: `03/04/2026`
  is 3 April in one convention and 4 March in the other. The system will not
  guess, because a wrong guess silently misdates a month of attendance and is
  not noticed until someone questions a payslip.
- The timezone the device's clock is set to, if it is not the work location's.

---

## 3. Registering the device

**Devices -> Add device** (needs the `device.manage` permission).

| Field | What to put |
|---|---|
| Code | Short, e.g. `GATE-1`. It appears in the webhook URL. |
| Name | What a person would call it, e.g. "Main gate reader". |
| How punches arrive | The adapter chosen in step 1. |
| Direction of each punch | See below. |
| Work location | Sets the timezone its naive timestamps are read in. |
| Field mapping | Which fields in its payload mean what. |

### Direction of each punch

Attendance needs to know whether a punch was an entry or an exit.

- **The device reports in or out** - choose this if its payload has a field
  saying so. Name that field, and map its values if they are codes rather than
  words (`0` and `1`, say). An unrecognised value is then **refused** rather
  than guessed, so the gap gets fixed instead of papered over.
- **Infer by alternating** - for a simple reader that only reports that a punch
  happened. The first punch of the day is an entry, the next an exit, and so on.
- **Entry-only** / **Exit-only** - for a single-purpose turnstile or gate.

### Field mapping

Name the fields exactly as the device writes them. Dotted paths reach into
nested data: `data.user.id`.

| Mapping | Required | Notes |
|---|---|---|
| Employee identifier field | Yes | The device's enrolment number for the person. |
| Timestamp field | Yes | |
| Timestamp format | Yes | Chosen from a list. Never guessed - see above. |
| Event id field | Strongly recommended | Lets the system recognise a re-sent punch exactly. Without one, an id is derived from the punch's content, which still deduplicates but cannot tell two genuinely identical punches apart. |
| Direction field | Only for "device reports in or out" | |

---

## 4. Setting up each adapter

### Webhook

1. Register the device. In the secret box, press **Generate** and **keep a copy**
   - it cannot be read back afterwards. It is stored encrypted, and a secret you
   can read from a screen is one that can leak through a screenshot or a support
   ticket.
2. Open **Devices -> (your device) -> What to configure on the device** and copy
   the URL.
3. On the device or its middleware, set the URL, and configure it to sign each
   delivery with an **HMAC-SHA256 hex digest of the request body**, in the
   `X-Device-Signature` header. A `sha256=` prefix is accepted.
4. Punch once on the device and check **Devices -> Reconciliation**.

Notes:

- The device must sign **the exact bytes it sends**. Re-serialising the body
  before signing produces a different digest and the delivery is rejected.
- An unsigned delivery is rejected. A device with no secret configured is
  rejected too - the endpoint never falls back to accepting anything.
- Retrying a failed delivery is safe. Punches are deduplicated, so a retry
  cannot double-count.
- You can add a source-address allowlist as a second layer, but it is never the
  only control: addresses are spoofable and proxies rewrite them.

### CSV upload

1. Register the device, choosing the delimiter your export uses.
2. Export the punch log from the device.
3. **Devices -> (your device) -> Upload log**.

Notes:

- Re-uploading a file that overlaps a previous one is safe. The overlapping
  punches are recognised as already known.
- A row that cannot be read is reported individually and stored. One bad row
  does not discard the file.

### REST polling

1. Register the device with its endpoint URL. The URL may contain `{cursor}` and
   `{limit}`, which are substituted on each poll.
2. If the device needs a credential, choose the authentication style and enter
   it. It is stored encrypted and never shown again.
3. If the device is on your own network, tick **This device is on our own
   network**. Without that, a private or loopback address is refused - on an
   internet endpoint it would indicate a misconfiguration or an attempt to make
   this server fetch something it should not. Cloud metadata addresses are
   refused either way.
4. Press **Poll now** to test.

For regular polling, turn on `devices.auto_sync_enabled` in Settings and run
`iams sync-devices` from a scheduled task. It is safe to run repeatedly.

---

## 5. Mapping identifiers to employees

Punches arrive carrying the device's enrolment number. Until that number is
linked to an employee, the punch is **stored and queued**, not discarded and not
applied.

**Devices -> (your device) -> Map an identifier.**

Set **Effective from** to the date the enrolment slot was actually assigned to
that person, not today. Mappings are dated because enrolment slots get reused:
if slot 47 belonged to one employee until March and another afterwards,
February's punches must still resolve to the first. Setting the date correctly
also makes already-stored punches resolvable.

To hand a slot to someone else: **close** the existing mapping with the last
date it applied, then add a new one. A second open mapping for the same
identifier is refused, because two would make a punch's owner ambiguous.

---

## 6. Fixing events that could not be used

**Devices -> Reconciliation** shows everything the device sent, including what
could not be used, with the reason and the original payload.

| Outcome | Meaning | What to do |
|---|---|---|
| Applied | Became an attendance punch. | Nothing. |
| Already known | Seen before and skipped. | Nothing - this is deduplication working. |
| No employee | The identifier is not mapped. | Map it, then **Reprocess the queue**. Or use **Attribute** on the row and tick "remember this identifier". |
| Unusable | Could not be interpreted or applied. | Read the reason. Usually the timestamp format or a field name is wrong - fix the mapping, then reprocess. |
| Not a punch | Matched an ignore rule, e.g. a heartbeat. | Nothing. |

Nothing the device sent is ever thrown away, which is what makes a mistake in
the configuration recoverable: correct it and reprocess the stored events. The
device does not need to send anything again.

Already-applied events cannot be reprocessed - doing so would duplicate the
punch.

---

## 7. Common problems

**Every delivery is rejected (401).**
The signature does not match. Check that the device signs the exact body bytes,
that the secret matches the one configured here, and that it is using SHA-256.
If the secret has been lost, set a new one here and on the device - deliveries
signed with the old one will then be rejected.

**Everything arrives as "No employee".**
The employee identifier field is probably mapped to the wrong field, or the
identifiers are not mapped yet. Open a queued row and look at "What the device
actually sent" to see the real field names.

**Punches land on the wrong date.**
Almost always the timestamp format. If the device writes `10/03/2026` meaning
10 March, the format must be day-first. Change it, then reprocess - the stored
events will be re-read with the corrected setting.

For an overnight shift, a punch landing on the *previous* date is correct, not a
fault: a shift that starts at 22:00 and ends at 06:00 is attributed to the date
it started, so a 06:00 punch belongs to the night before.

**Punches are rejected as being in the future.**
The device's clock is ahead. Correct it on the device. The tolerance is
configurable per device, but raising it is the wrong fix - accepting a punch
from the future credits time that has not been worked.

**The device shows as "gone quiet".**
It has stopped reporting. This is worth acting on promptly: a silent device
raises no error of its own, it just leaves a growing hole in attendance that
would otherwise be discovered at month end. Check its power and network, then
check what it last sent in the synchronisation log.

**A synchronisation shows as failed.**
The reason is in **Devices -> (your device) -> Synchronisation log**. Every
attempt is recorded there, successful or not.

---

## 8. What an administrator should know

- The webhook endpoint is the only part of this system that accepts a write
  without a signed-in user. It is secured by the HMAC signature, it can only
  create events for the one device named in its URL, and a failed
  authentication is recorded in the audit log.
- Device secrets are encrypted with the field encryption key, which is held
  separately from the token-signing key. They are never returned by the API, and
  the read-only database role cannot read those columns.
- What a device sent is immutable in the database. The processing columns change
  as an event is reconciled, but the payload, timestamp and identifier as
  delivered cannot be rewritten - a reconciliation screen must not be able to
  edit the evidence it exists to explain.
- `devices.quiet_alert_after_minutes` sets how long a device may go silent
  before it is flagged. `devices.auto_sync_enabled` and
  `devices.auto_sync_interval_minutes` govern scheduled polling only; neither
  affects web punching.
