# Take-home: from a shipping inbox to a demurrage answer

## The problem

Halcyon Tankers runs a small fleet of oil-product tankers. Every vessel on a voyage generates a
steady stream of email: the broker's contract recap, agents' arrival notices, the master's position
reports, port logs, invoices, line-ups, and plenty of mail that has nothing to do with that voyage.

An operator at Halcyon has to answer one question for every voyage, every day:

> **Where is the vessel, how much laytime has been used at each port, and are we owed demurrage?
> If so, how much, and on what evidence?**

Today they answer it by reading every email by hand and rebuilding the numbers in a spreadsheet.
Your job is to build the AI pipeline that does that reading and produces the answer.

If the shipping vocabulary is new to you, read `DOMAIN.md` first. It explains every term and the
laytime arithmetic in plain language. Nobody expects you to be a shipping expert.

## What you get

```
TASK.md      this file
DOMAIN.md    shipping vocabulary and how laytime / demurrage is calculated
fleet.json   Halcyon's vessels, with the one active voyage and its planned itinerary
emails/      19 emails (.eml, plain text), numbered in the order they arrived
```

All data is synthetic. Companies, people and email addresses are fictional.

In this sample only **one vessel is on a voyage**, and the inbox holds fewer than 20 emails. The
real inbox covers hundreds of vessels and thousands of emails a day, in formats that change based
on every port, captain, ...
**Build as if this sample is the first of many:** your pipeline should not depend on the
particular wording, senders or ordering of these emails.

## What to build

A pipeline you can run on the `emails/` folder that produces, for the active voyage:

1. **Which emails matter.** For each email: is it about one of our vessels, which one, and is it
   relevant to the voyage's schedule or laytime?
2. **The contract terms** that govern laytime and demurrage, taken from the emails.
3. **The port calls:** when the vessel arrived at each port (end of sea passage) and when it
   sailed.
4. **Laytime per cargo port:** when it started, when it ended, what was deducted and why, and how
   many hours were used.
5. **The demurrage result:** total laytime used against laytime allowed, and the amount owed.
6. **Evidence:** every date, time and number above must be traceable to the email(s) it came from.
7. **Things a human should check:** anything your pipeline is unsure about or found contradictory.

Produce a machine-readable output (for example JSON) and a short brief an operator could read in
one minute. The shape of both is your call.

You are not given an answer key. Part of the work is deciding what "correct" looks like.

## What we are looking for

We care much more about **how you built the pipeline and how you know it works** than about
coverage or polish. In particular:

- How you split the work between AI models and ordinary code, and why.
- How you deal with messy inputs: irrelevant mail, conflicting information, updates that
  replace earlier ones.
- How you know your output is right, and how you would know if a prompt or model change made it
  worse.
- How you chose your model(s), and what a run costs.

A smaller pipeline that you can show is correct beats a bigger one that you can't.

## Out of scope

- A user interface, a database, a deployed service, authentication.
- Scale and performance. A run on 19 emails can be slow and sequential.
- PDF or other attachments. All emails are plain text.

## Practicalities

- **Time:** we expect around 10–12 hours spread over the week, and please don't spend too long.
  Unfinished is fine; tell us what you would do next.
- **Tools:** any language, libraries and frameworks you like. AI coding assistants are welcome;
  tell us briefly how you used them.
- **Model access:** we will send you an OpenRouter API key with a spending cap. It gives you access
  to most commercial and open-weight models. You should need only a fraction of the cap.
- **Questions:** if something is ambiguous, make a reasonable assumption, write it down, and move
  on. You can also email us.

## What to send back

A GitHub repository (history included) containing:

- your code, with instructions to run it in one or two commands;
- the output of a run on the full `emails/` folder;
- a `README.md` of about one page covering:
  - your design and the main decisions behind it;
  - how you know the results are correct, and how good they are;
  - which models you used, why, and what a run costs;
  - what doesn't work yet, and what you would do with another week;
  - how this would need to change to run every day on a real inbox with many vessels.
