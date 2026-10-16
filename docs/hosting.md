# Free hosting evaluation

Written for whoever decides where this system runs. It evaluates free tiers for
the three parts separately - the frontend, the backend and the database -
because their requirements are not alike, and it ends with a recommendation that
is not "use the free tier".

**Researched on 16 October 2026.** Every figure below came from secondary
sources - comparison articles, not vendor pricing pages - and free tiers change
without notice: Fly.io withdrew its free allowance in October 2024, Render cut
free Postgres from 90 days to 30 in May 2024, and Koyeb began requiring a card
hold in February 2026. **Confirm every number against the provider's own
pricing page and terms before committing.** Treat this document as a framework
and a set of questions, not a current price list.

---

## 1. What this system actually needs

| Part | Needs |
|---|---|
| Frontend | Static file hosting. A 494 kB entry bundle plus per-page chunks. No server-side rendering, no serverless functions. Trivial to host. |
| Backend | A long-running Python process. Scheduled jobs (leave accrual, comp-off, expiry, announcements, attendance notices). Persistent disk for uploaded documents. Outbound access to the database. |
| Database | PostgreSQL 17 with `JSONB`, partial indexes, `NULLS NOT DISTINCT` unique constraints, `plpgsql` triggers and `NUMERIC` arithmetic. Durable storage. Restorable backups. |

Two of those cause most of the trouble on free tiers: **a long-running process**
and **durable Postgres**. Static hosting is nearly free everywhere.

## 2. Frontend

| Provider | Free allowance | Commercial use | Verdict |
|---|---|---|---|
| Cloudflare Pages | Unlimited bandwidth, 500 builds/month | Permitted | **Best fit.** Static files only is exactly what this frontend is. |
| Netlify | 100 GB bandwidth, ~300 build credits/month | Permitted | Workable. The credit pool makes frequent deploys the limiting factor, not traffic. |
| Vercel (Hobby) | 100 GB bandwidth | **Not permitted for commercial use** - the Hobby plan is for personal projects | **Rules itself out.** Ishvara Tech Consulting LLP's own HRMS is commercial use, whatever its traffic. |

**Recommendation: Cloudflare Pages or Netlify.** The frontend is the one part
where a free tier is genuinely appropriate: it serves static files, holds no
data, and an outage degrades access rather than losing anything. Note that the
built bundle still talks to the backend over the network, so the API's origin
must be configured in CORS and served over TLS.

## 3. Backend

| Provider | Free allowance | The catch |
|---|---|---|
| Render | 750 instance hours/month per workspace | **Spins down after 15 minutes without traffic**, and the next request waits roughly a minute for a cold start |
| Koyeb | One service, 512 MB, 0.1 vCPU, Frankfurt or Washington only | Scales to zero after an hour idle; since February 2026 a card with a $29 hold is required, and sign-up defaults to the paid plan |
| Railway | $1 of credit a month after a 30-day trial; 1 project, 3 services | Services **pause** when the credit runs out, and credit does not roll over |
| Fly.io | A trial only - 2 VM-hours or 7 days - for accounts created after October 2024 | No ongoing free tier; the old allowance is grandfathered to legacy accounts |

Three problems make all of these a poor fit for this backend specifically:

**Cold starts break the first punch of the day.** An employee arriving at 09:28
is the first traffic in fourteen hours. On Render's free tier their punch waits
about a minute, and the natural response to a button that appears not to work is
to press it again - which this system handles correctly (punches alternate
direction and duplicates are rejected), but which no employee should have to
learn. Attendance is the one feature where latency is a product problem rather
than an annoyance.

**Scheduled jobs need something awake.** Leave accrual, comp-off expiry,
carried-leave expiry, announcement scheduling and the unsettled-attendance
notice are all cron entries. A free instance that sleeps has nothing to run
them, and the free tiers' own cron support is inconsistent between providers
and between the sources describing them. Every job in this system is
idempotent, so a missed run can be caught up by running it later - but
somebody has to notice.

**Uploaded documents need a disk that survives a restart.** Employment
documents and announcement attachments are stored on the filesystem.
Container-based free tiers generally have ephemeral storage, so a restart loses
them while the database still references them - the worst shape of failure,
because the records look intact. Either attach a persistent volume (usually not
free) or move storage to an object store, which is a code change this project
has not made.

**Recommendation: do not run the backend on a free tier.** A small paid
instance with a persistent disk removes all three problems at once, and it is
the cheapest part of being able to trust the system.

## 4. Database

| Provider | Free allowance | The catch |
|---|---|---|
| Render Postgres | 1 GB | **Expires 30 days after creation**, then a 14-day grace period before the database and its data are deleted. One free database per account. |
| Neon | ~1 GB per project, ~100 compute-hours/month | Scale-to-zero cannot be disabled on the free plan: compute suspends after 5 minutes idle, adding a wake-up delay to the first query. Exhausting compute-hours suspends it until the next period. |
| Supabase | 500 MB database, 5 GB egress, up to 2 projects | **Projects pause after about a week of inactivity** and must be resumed manually. |

**Render's 30-day expiry disqualifies it outright** for anything holding payroll
records: a free database that deletes itself a fortnight after a reminder nobody
read is not a database, it is a deadline. Neon's and Supabase's idle behaviour
is survivable for a pilot but adds an unpredictable first-query delay in front
of every sign-in.

Size is not the binding constraint. This schema with a year of attendance for
fifty employees is comfortably inside 500 MB. The constraints that matter are
**durability, backups and the right to hold the data at all**.

## 5. Whether the terms permit confidential payroll data

This is the question the brief asks to be answered explicitly, and the honest
answer is: **the research above does not answer it, and nobody should assume it
does.**

None of the sources consulted covered data-processing terms, sub-processor
lists, breach-notification commitments, data residency or certifications. Those
live in each provider's terms of service and data processing agreement, and they
are the only documents that matter here.

Three specific things must be checked against those documents, not against a
comparison article:

1. **Is commercial use permitted at all?** Vercel's Hobby plan is not for
   commercial use, and a company's own HRMS is commercial use. A free tier whose
   terms you are breaching is not a foundation for a payroll system - the
   provider may suspend it without warning, and you would have no recourse.
2. **Where does the data sit, and is that acceptable?** This system holds
   salaries, bank account numbers, PAN and Aadhaar fragments for employees in
   India. Data residency, the company's own obligations under Indian law, and
   any client contracts all bear on whether a US or EU region is acceptable.
   **This is a legal question, not a technical one, and this project has not
   been given an answer to it.**
3. **What happens to the data when the free tier ends?** Render deletes a free
   database 44 days after creation. A provider that deletes data on a schedule
   is incompatible with statutory retention of employment records, which runs to
   years.

Alongside those, free tiers generally offer **no uptime commitment, no support
and no backup guarantee**. The system's own backup script covers the third,
provided somebody runs it and stores the dump somewhere the provider does not
control - and that dump is unencrypted by design, so where it lives is itself a
decision.

## 6. Recommendation

**For a demonstration or a pilot with invented data:** Cloudflare Pages for the
frontend, Koyeb or Render for the backend, Neon or Supabase for the database.
Accept the cold starts, run the scheduled jobs manually, and understand that
uploads may not survive a restart. Nothing confidential goes in.

**For real payroll, for real employees:** a small paid instance with a
persistent disk, a managed Postgres with automated backups and a retention
window the company has chosen, and TLS terminated at a proxy that also provides
the per-IP rate limiting the application does not
(see [security.md](security.md)). The total is small - the cost of a few coffees
a month - and it removes every objection above.

**What this project will not do is claim the free path is suitable and leave the
consequences to whoever deploys it.** The system runs on either; only one of
them is honest about holding other people's salaries.

## 7. What to verify before deciding

A checklist for whoever makes the call, because every number above has a
shelf life:

- [ ] Read each candidate provider's **terms of service** on commercial use.
- [ ] Read the **data processing agreement** and the sub-processor list.
- [ ] Confirm the **region** and whether its location is acceptable for Indian
      employee data under the company's own obligations.
- [ ] Confirm the **database retention and deletion policy** on the plan chosen,
      including what happens if a card expires.
- [ ] Confirm whether **persistent storage** is available, and at what cost.
- [ ] Confirm **cron or scheduled job** support, or arrange an external
      scheduler.
- [ ] Test a **cold start** from a genuinely idle instance and time it, with the
      09:30 punch in mind.
- [ ] Run `python infra/backup/backup.py verify` **against the deployed
      database**, not just locally.
- [ ] Decide where backups are stored, and whether they are encrypted.
- [ ] Point `IAMS_DATABASE_URL` at the `iams_app` role, not the owner, so the
      per-table revocations take effect.

---

Sources consulted, all secondary and all dated October 2026:
[State of Free Hosting 2026](https://snapdeploy.dev/state-of-free-hosting),
[Render free tier details](https://unanswered.io/guide/render-free-tier-details),
[Render free Postgres 30-day expiry](https://bex.co/blog/2026/09/23/render-free-postgres-30-day-expiry),
[Neon vs Supabase 2026](https://designrevision.com/blog/supabase-vs-neon),
[Fly.io alternatives 2026](https://www.buildmvpfast.com/alternatives/fly-io),
[Railway alternatives 2026](https://livemy.app/blog/railway-alternatives),
[Netlify vs Vercel vs Cloudflare Pages](https://toolchase.com/blog/netlify-vs-vercel-vs-cloudflare/).
