# Culture Digest

A personalised culture and things-to-do newsletter. Readers fill in a short
onboarding questionnaire (age, where they live, where they love to travel
to, interests, taste, budget, availability, openness to a wildcard pick),
editors curate a database of opportunities (theatre, music, film,
exhibitions, talks, food, events, unusual experiences), and a matching
engine picks a small number of high-confidence recommendations for each
reader and emails them a newsletter. Feedback links (`More like this`,
`Not for me`, `Save`, `Booked`) close the loop and improve future picks.

Only email is required at signup — every other field is optional, and the
matching engine treats an unanswered question as "no preference" rather
than excluding the reader from getting recommendations.

Stack: Django 5.1 (Python 3.12), SQLite for local dev, [Resend](https://resend.com)
for email delivery. The reader-facing pages (signup, thank-you, unsubscribe,
feedback confirmation) use a hand-built Apple.com-style design system —
big type, scroll-reveal animations, gradient hero orbs, segmented pill
controls, an iOS-style toggle — in [templates/base.html](templates/base.html),
plain CSS/JS with no build step or framework. The newsletter *email* borrows
the same typography and colour language but is necessarily static (no
animation, no backdrop blur) since email clients don't render those
reliably.

## Why Django

Curation and tagging are expected to stay largely editorial for the MVP,
with AI assisting research/classification/writing rather than replacing
editorial judgement. Django's built-in admin gives editors a solid,
zero-build interface for managing opportunities and tags from day one
(`/admin/`), so no custom back-office UI had to be built to prove the MVP.

## Project layout

```
config/                   Django project settings, root URLconf
readers/                  Reader profile model, onboarding questionnaire
opportunities/            Opportunity + Tag models, editorial admin
recommendations/          Matching engine, email rendering/sending, feedback
templates/
  base.html                Shared layout for the public-facing pages
  onboarding/               Questionnaire, thank-you, unsubscribe pages
  emails/                   The newsletter itself (HTML + plain text)
  feedback/                 "Thanks, noted" confirmation page
```

### Data model

- **`readers.Reader`** — one row per subscriber. Only `email` is required;
  everything else (name, age, location, travel destinations, travel radius,
  budget, availability, interest tags, mainstream/unusual and
  intimate/large-scale sliders, openness to a wildcard pick, free-text
  "loved"/"disliked" examples) is optional and can be filled in gradually.
- **`opportunities.Opportunity`** — one row per curated opportunity:
  category, description, editorial note, tags, price tier, location,
  booking URL, critic rating, mainstream/unusual and intimate/large-scale
  ratings, and a draft/published/archived status for editorial workflow.
- **`opportunities.Tag`** — shared taste/interest vocabulary used by both
  readers (what they're into) and opportunities (what they're tagged with).
  Overlap is the main matching signal.
- **`recommendations.NewsletterIssue`** — one newsletter send to one reader.
- **`recommendations.Recommendation`** — one opportunity recommended within
  one issue, with the rationale shown to the reader, an internal score, and
  whatever feedback they gave.

### Matching engine (`recommendations/matching.py`)

Deliberately simple and rule-based for the MVP:

1. Filter to published, non-expired opportunities the reader hasn't been
   shown recently (cooldown window, default 60 days).
2. Score each candidate on: tag overlap with the reader's interests, budget
   fit, location/travel-radius fit, mainstream-vs-unusual and
   intimate-vs-large-scale closeness, and critic rating.
3. Add a **learned adjustment** from the reader's past feedback: tags on
   opportunities they said "More like this"/"Save"/"Booked" about score
   higher; tags on "Not for me" picks score lower, and a *repeated* strong
   dislike drops that cluster entirely. This is the whole "gets better over
   time" loop for the MVP — no ML model needed yet.
4. Return the top N (default 4). If a reader doesn't have enough strong
   matches, they're skipped for that send rather than getting a padded-out,
   low-confidence newsletter — quality over volume was the point.
5. If the reader opted into **"Surprise me sometimes"**, one slot is swapped
   for the best-scoring opportunity that shares none of their stated
   interest tags — a genuine wildcard, not just a lower-confidence version
   of their usual picks.

Since every onboarding field is optional, an unanswered question is treated
as "no preference" rather than a reason to exclude the reader: no stated
budget means no price penalty, no stated location means nothing is filtered
out on distance, and so on. A reader who filled in nothing but their email
still gets matched against the full published catalogue.

`build_rationale()` turns a match into the reader-facing "why this suits
you" copy, currently templated off the editorial note plus the top scoring
reasons. **This and the scoring function are the natural places to plug in
an AI-assisted matcher/writer later** — the calling contract is designed to
stay stable while the internals get smarter.

### Email delivery (`recommendations/emailing.py`)

Thin wrapper around the Resend SDK. If `RESEND_API_KEY` is unset, sending
automatically falls back to a dry run (renders and logs, never calls the
API) so the app works out of the box without an account.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
```

> Python version note: this project pins to Python 3.12. Django 5.1 hits a
> template-rendering bug on very new Python builds (3.14) in the admin's
> `change_list_object_tools` tag — use 3.12 or 3.13 until Django certifies
> newer Pythons.

Seed some sample tags, opportunities, and a demo reader:

```bash
python manage.py seed_demo_data
```

Run the dev server:

```bash
python manage.py runserver
```

- `/` — the onboarding questionnaire
- `/admin/` — editorial curation of opportunities/tags, and read access to
  readers, newsletter issues and recommendations
- `/r/<token>/<action>/` — feedback links embedded in emails
  (`more-like-this`, `not-for-me`, `save`, `booked`)
- `/unsubscribe/<token>/` — one-click unsubscribe

## Sending newsletters

```bash
# Dry run: computes matches, renders the email, logs it, and touches
# nothing in the database (safe to re-run repeatedly while testing).
python manage.py send_newsletters --dry-run

# Real send for one reader (useful while testing against a live Resend key)
python manage.py send_newsletters --reader you@example.com

# Real send to everyone active, at most 4 recommendations each
python manage.py send_newsletters
```

There's no scheduler wired up yet — in production this command should run
on a periodic job (e.g. a daily/weekly cron or a Railway cron service).

## Tests

```bash
python manage.py test
```

Covers the matching engine: budget/location/date filtering, tag-overlap
scoring, the feedback learning loop, and rationale generation.

## Known MVP limitations / next steps

- **Location matching is string equality on a free-text area field** (e.g.
  "London" == "London"), not geocoding or distance. Fine for a single-city
  pilot; will need real geo matching to expand to multiple cities.
- **No scheduler** — `send_newsletters` needs to be invoked by a cron job.
- **No email opens/click tracking beyond the feedback links** — Resend
  supports this natively if/when it's worth wiring up.
- **No auth for readers** — feedback/unsubscribe links use unguessable
  UUID tokens instead of accounts, which is intentional for a
  frictionless MVP but means links must not leak (e.g. don't forward the
  email publicly).
- **Rationale generation is templated, not AI-written** — the
  `build_rationale()` / matching contract is the intended integration point
  for that later.
