# The Open Case Philosophy

This document explains why Open Case is designed the way it is.
These are not guidelines — they are the reasoning behind structural decisions
that are otherwise just rules without context.

---

## Why "Receipts, Not Verdicts"

The justice system has a burden of proof standard for good reasons.
A platform that tells millions of people that a specific official is corrupt —
without a trial, without discovery, without adversarial process — is not
accountability infrastructure. It is an accusation machine.

Open Case is not in the business of verdicts.

The receipt documents what the public record shows. The donation happened.
The vote happened. The days between them are what they are. The contract was
awarded without competitive bidding. These are facts, not conclusions.

What those facts mean — whether they represent corruption, coincidence, or
legitimate policy preference — is for prosecutors, journalists, and courts.
Our job is to make sure those facts are findable, documented, signed,
and impossible to quietly disappear.

This isn't legal caution. It's epistemic honesty.

---

## Why Cryptographic Signing

The public record is surprisingly easy to make disappear. Websites get taken
down. PDFs get deleted. Databases get "updated." Officials who are under
investigation have been known to make things harder to find.

A signed, timestamped receipt chain means: at this moment in time, this specific
document said this specific thing, and it was found at this specific source.
If the source later changes or disappears, the receipt still stands.

This is what chain of custody means in evidence law. We built the digital version.

The signature also means the investigator cannot be accused of fabricating
the evidence. The signature is tied to the investigator's public key.
The document was what it was when they signed it.

---

## Why the Game Layer Exists

Civic investigation is important and boring. Most of the people who care
about accountability don't have the tools, the access, or the patience to
dig through FEC filings. The people who do have the tools are often already
working investigative journalists who don't need another platform.

The game layer is an on-ramp. Credibility scores, rank progression, and the
social dynamics of "who found what first" are not decoration. They are the
mechanism by which regular people learn investigative skills without being
told they're learning investigative skills.

QAnon worked because it made people feel like investigators. It had the
dopamine loop right — the finding, the sharing, the belonging to a group
that sees clearly. It got the content catastrophically wrong.

Open Case keeps the dopamine loop and changes the content to actual public
records. The feeling of finding something real should be more compelling
than the feeling of finding something fictional. We're betting on that.

---

## Why the Case File Looks Like a Police Report

Most people have never filed an SEC complaint, submitted an IG tip, or
worked with a congressional oversight office. But almost everyone has an
intuitive understanding of what a police report is and what it means.

It means: someone did the work. Someone documented the facts. Someone signed their name.
This went through a process.

The police report format borrows that authority and applies it to citizen
investigation. When someone opens a case file that looks like a police report,
they immediately understand that this is different from a tweet, different from
a Facebook post, different from a Reddit thread. Someone did the work here.

The format is a trust signal.

---

## Why Documented Absence Matters

"We looked here and found nothing" sounds like failure. It isn't.

If an official who controls billions in federal contracts has no lobbying contacts
in the Senate LDA database, no FEC donations from contractors in their jurisdiction,
and no USASpending records showing unusual patterns — that's meaningful. It means
the influence is invisible, which is itself a finding.

If we only document what we find, we create a bias toward officials who are
visible — who have the most connections, the most donations, the most records.
The official with almost no traceable connections might be the cleanest person
in Congress, or they might be the most skilled at keeping things off the record.
Documented absence is how we distinguish.

Every source check gets logged. Every empty result gets a `gap_documented` entry.
The receipt shows not just what was found but what was searched.

---

## Why the Pickup / Handoff Mechanic

Most investigations die because one person gets stuck and nobody picks them up.

A journalist hits a wall. A researcher runs out of time. An investigator doesn't
have the specialized knowledge to get the next piece. The work stops.

The pickup mechanic exists because accountability is a relay race, not a sprint.
The person who opens the case doesn't have to close it. The person who closes it
doesn't have to have done all the work. Every contributor is credited in the
chain of custody.

This also means investigations don't die when the original investigator stops.
A stalled case with good evidence is a resource. Someone who knows Indiana state
procurement law might find a case that's been sitting for six months and unlock
the next step in twenty minutes.

The platform holds the work. People come and go.

---

## Why We Don't Authenticate Investigators (Yet)

Pseudonymity is a feature, not a bug.

Investigators who surface real misconduct by powerful people become targets.
SLAPP suits — bad-faith defamation claims designed not to win but to impose
costs and create chilling effects — are real. Reporters have been fired.
Sources have been prosecuted.

A platform that requires real identity to participate will not be used by the
people who most need to use it. A local government employee who knows where
the bodies are buried cannot put their name on a case file if their boss
is the subject.

The credibility score system is designed to make pseudonymous reputation real.
Your handle's track record — how many receipts you've completed, how many signals
you've confirmed that held up to review — is your credibility. It doesn't require
your name.

Authentication is coming, but it will be an option, not a requirement.
And when it comes, the identity won't be stored by the platform in a form
that can be compelled by subpoena.

---

## Why Public Records Only

The temptation in civic investigation is always to go beyond public records —
to leak internal documents, to use private databases, to aggregate information
about individuals that they haven't disclosed.

We don't do that, and it's not just legal caution.

It's epistemological. The value of a public record is that it can be verified
by anyone. If Open Case surfaced information that only we had access to, the
receipt would be worth nothing — it couldn't be reproduced, couldn't be confirmed,
couldn't be trusted.

The public record is weaker than a leaked document. It's also stronger in the
only way that matters: it's verifiable. A receipt built entirely from public
records is a receipt that stands forever because anyone can check the math.

---

## The Hardest Design Decision

The hardest thing to build into the system is the thing that makes it most
different from everything else: the platform must be equally useful for
investigating officials of any political party.

A platform that surfaces corruption selectively — where the algorithm
is more likely to surface findings about one side than the other — is not
accountability infrastructure. It's opposition research with a transparency wrapper.

The detection engines are designed to be indifferent. Temporal proximity
between a donation and a vote is suspicious regardless of which party the official
belongs to. A no-bid contract is suspicious regardless of which administration awarded it.

The hard enforcement of this is in peer review. When a signal is reviewed by
investigators from different domains and different trust clusters, it's harder
for ideological clustering to produce systematic bias. A chain that survives
adversarial review by people who disagree with each other is stronger than
a chain that survived a sympathetic audience.

We don't always get this right. But it's the design target, and deviations
from it are bugs, not features.

---

*Open Case is built by Nikodemus Systems.*
*"The honesty is the product."*
