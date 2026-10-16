# User guide

Written for: employees, managers, and HR / administrators.

The system shows each person only what their role allows, so if a page described
here is not in your sidebar, your account does not have it. That is not a fault
to report unless you believe it should.

---

## Making it comfortable to look at

Click your avatar in the top right. Two of the rows open a small list when you
point at them:

- **Display mode** -- Light, Dark, or System, which follows whatever your
  computer is set to and changes with it during the day.
- **Theme colour** -- eight colours. A tick marks the one in use.

Either takes effect the moment you click it. There is nothing to save.

These are remembered in the browser you are using, not against your account, so
your choice at your desk does not follow you to a shared machine - and somebody
else signing in on your computer sees your settings, which is a cosmetic matter
only. Nothing here changes what any page says or who can see it. Reds, greens
and ambers keep their meaning in every colour: an approved item is green and a
problem is red, whichever you pick.

---

## For employees

### Signing in

Your administrator creates your account and gives you a temporary password,
handed over directly rather than emailed - this system does not send email. You
must change it the first time you sign in, and anything else is blocked until
you do.

Your password is never shown to anybody, including administrators. If you forget
it, HR issues a new temporary one; they cannot look up the old one.

### Recording your attendance

**My attendance** has one button. Press it to punch in; press it again to punch
out. It alternates, so there is nothing to choose and no way to record the wrong
direction.

Three things worth knowing:

* **The time comes from the server, not your device.** Changing your computer's
  clock does nothing.
* **Pressing it twice by accident is safe.** A duplicate is rejected rather than
  recorded.
* **Forgetting to punch out does not cost you a day.** The day is marked as
  needing attention and is **paid in full** while it is sorted out. The system
  never treats a missing punch as absence.

If a day is wrong, raise a **correction** from the same page. It goes to your
manager, then to HR. You will be told the outcome either way, and if it is
declined the reason is included.

### Leave

**Leave** shows your balance per type, what you have taken, and what is pending.
The balance is calculated from an append-only ledger, so every day in or out of
it can be explained - you can see the opening balance, each accrual, and each
deduction.

When you apply:

* The system tells you how many days it will cost **before** you submit,
  including whether any of it falls on a holiday or a weekly off.
* It refuses a request you do not have the balance for, unless your company's
  policy allows a negative balance.
* It refuses a date more than a configured number of days in the past - ask HR
  to record an older one, which they can.
* You cannot apply for a day you already have leave on.

You will be notified when each stage of the approval decides, not only at the
end: "your manager approved it, HR is next" is worth knowing while you are
planning.

**Cancelling approved leave** returns the days to your balance. Both movements
stay on the ledger, so the balance always adds up.

### Comp-off

If you work on a weekly off or a holiday, you may earn a **comp-off credit** -
whether you do, and how many minutes it takes, is your company's configuration.

Comp-off is listed credit by credit, not as a single total, because **each
credit expires on its own date**. One total would hide a day about to be lost.
When you request comp-off, the system spends the credit expiring soonest first,
so you never lose one you could have used.

A credit may need HR to verify it before you can use it. Until then it is shown
separately, as awaiting verification, rather than as available - so you do not
plan around a day you may not get.

### Holidays

**Holidays** shows the list that applies to you, which depends on your work
location and any groups you are in. If your company's list includes optional or
restricted holidays, you choose yours here, up to the limit it allows. A day you
have not chosen is a normal working day for you.

### Announcements and notifications

The **bell** in the top bar shows unread notifications: leave and comp-off
decisions, attendance corrections, shift changes, holiday lists and payslips.
You can mute a category you do not want from the notification centre; nothing is
muted by default.

**Announcements** are company notices. One marked critical asks you to
acknowledge it explicitly - opening it is not the same as confirming you have
read it, and the difference is recorded.

### Payslips

**Payslips** shows your salary structure and your released payslips. A payslip
appears only once the payroll run has been finalized and released: until then
the figures could still change, and watching your own pay move would be worse
than waiting.

Every line says **what it was computed from**. Not "Provident fund 1800" but
"10% of a PF wage of 15000, the configured ceiling". If a figure looks wrong,
that line is what you quote when you ask.

Employer contributions are shown separately and are **not** deducted from your
pay. They are what the company contributes on top.

---

## For managers

Everything above applies to you as well. In addition:

**Approvals.** Leave, comp-off and attendance corrections from your own reports
arrive in your queue and in your notifications. You see your team's requests
because they report to you - not because of a general permission - so the
system checks the relationship, not just your role.

**You cannot approve your own request**, whatever permissions you hold. Yours
goes to your own manager or to HR.

**What you can see.** Your team's attendance, leave balances and comp-off
position. **Not their salaries or payslips** - pay is more sensitive than
attendance and is kept separate, so being somebody's manager does not give you
access to what they earn.

**Reports.** If you have report access, your reports cover your team. The report
page says so, which matters if you ever compare totals with HR's copy of the
same report.

---

## For HR and administrators

### What to set up, and in what order

See [operations.md](operations.md) section 1. In short: organisation, shifts and
a calendar, holidays, leave types and policies, employees, salary components and
revisions, statutory rules. The system refuses the later steps until the earlier
ones exist, and says which is missing.

### What is deliberately empty

Nothing in this system is pre-filled with company policy or legal figures:

* **No leave types.** Statutory entitlements have mandated quotas this project
  has not been given verified references for.
* **No holidays.** Dates vary by year, state and company.
* **No salary components.** "Basic" and "HRA" look universal, but their
  proportions are a company decision with tax consequences.
* **No statutory rates, ceilings or slabs.** A rate entered without a verified
  source produces a wrong deduction that looks right.

Where a setting has a working default, it is marked as unconfirmed until
somebody reviews it. **Settings > health** lists what is outstanding, and
`iams config-status` reports the same from the command line.

### Attendance

**Recompute** rebuilds days from their raw punch events. It is safe to re-run,
honours the lock on old attendance, and preserves days you have pinned. Both
guards can be waived explicitly, and the audit entry records that they were.

**A day with no attendance record is not an absence.** Payroll pays it and flags
it. If somebody genuinely did not work, record it - do not rely on the absence
of data, which is indistinguishable from nobody having looked.

### Leave

Recording leave **on behalf** of an employee is a separate action, flagged as
such on the application so the record shows who raised it. It is the documented
path for a backdated entry, and it is exempt from the window that applies to
employees - which is why the refusal they see says to ask you.

**Year-end closing** moves everybody's entitlement at once. Run the preview
first: it shows, per employee and leave type, what will be carried, encashed and
lapsed, and those three always add back to the closing balance. Re-running after
applying posts nothing further.

### Payroll

The lifecycle is deliberate: **draft, calculated, reviewed, approved,
finalized**, each a separate permission.

* **Nobody may review a run they calculated**, or approve one they reviewed. One
  pair of eyes over a whole company's pay is not a review.
* **A reviewed run cannot be recalculated.** Send it back first, with a reason -
  otherwise the numbers would move under the reviewer without anybody knowing.
* **Review flags are shown, not blocking.** A negative net pay is occasionally
  correct, so the system shows it rather than refusing it. Read them before
  approving.
* **A finalized run cannot be changed.** It is the record of what was paid. A
  correction is an adjustment applied in a later run, with the month it belongs
  to recorded separately from the month that pays it.
* **Releasing payslips is separate from finalizing**, so the register can be
  ready for the bank before everybody sees their payslip.

**Statutory rules.** Each domain needs a rule set with its rates entered and
**verified against the published source you took them from**. Until then the
figures compute - so you can see their effect - but the run cannot be finalized.
Once verified, the figures are frozen: a change is a new effective-dated
version, because the verification says somebody checked *those* numbers.

The system does not transfer money and does not file returns. It produces the
register and the per-domain totals those are prepared from.

### Reports and exports

Ten reports, each narrowed to the employees you may already see. **Exporting is
a separate permission from reading**, and every export is recorded with who took
it and which filters - a spreadsheet leaves the system's access controls behind,
and that entry is the only thing that makes the file traceable afterwards.

The employee register deliberately contains no bank details, PAN or Aadhaar.

### The audit trail

**Audit and retention** shows who changed what, with before and after values.
Searching it is itself recorded, with the filters you used - reading the trail is
a privileged act, and an organisation may need to account for it.

The trail cannot be edited or deleted through the application, and the database
refuses it too.

**Retention** shows how much data is held beyond each configured period.
Nothing is purged automatically: a retention period destroys evidence as well as
data, so it is a deliberate command and the periods start unconfirmed.

---

## Getting help

Every error in this system carries a **request id**. Quote it: it lets whoever
is helping you find the exact request in the logs and in the audit trail, which
is faster and more reliable than describing what you were doing.
