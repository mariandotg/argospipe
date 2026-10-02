# ATS fixtures

Captured 2026-10-01 with `curl -m 60 -A "argospipe-fixtures/0.1"`. Public job-board APIs only.
Unit tests read these files and never call the network.

## URLs

| ATS | Fixture | URL |
|---|---|---|
| Greenhouse | `greenhouse/datadog.json` | `https://boards-api.greenhouse.io/v1/boards/datadog/jobs?content=true` |
| Greenhouse | `greenhouse/gitlab.json` | `https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true` |
| Lever | `lever/dlocal.json` | `https://api.lever.co/v0/postings/dlocal?mode=json` |
| Lever | `lever/yuno.json` | `https://api.lever.co/v0/postings/yuno?mode=json` |
| Ashby | `ashby/nubank.json` | `https://api.ashbyhq.com/posting-api/job-board/nubank` |
| Ashby | `ashby/supabase.json` | `https://api.ashbyhq.com/posting-api/job-board/supabase` |
| Greenhouse | `greenhouse/empty.json` | `.../boards/test/jobs?content=true` (200) |
| Lever | `lever/empty.json` | `.../postings/demo?mode=json` (200) |
| Ashby | `ashby/empty.json` | `.../job-board/deel` (200) |
| all | `<ats>/not_found.json` | slug `argospipe-nope-xyz` (404) |

Empty slugs are found by probing, not by design. They can start to hold postings. The fixtures keep the captured bodies.

## How trimmed

- Keep the first 3 postings of the list. Keep every field of each posting, unchanged.
- Pretty-printed with 2 spaces (`json.dumps(indent=2, ensure_ascii=False)`).
- Greenhouse `meta.total` keeps the **real** total (442 / 205), so it does not match the 3 jobs kept.
- `empty.json` and `not_found.json` are the raw bytes (compact, no trailing newline).
- Raw sizes before trim: Greenhouse 3–5 MB, Ashby 0.6–1.7 MB, Lever 0.6–0.8 MB.

## Differences vs the spec table

- **Greenhouse.** `.jobs[]` and `meta.total`. No pagination: one response holds all jobs. `?content=true` is required for the description. Without it, `content`, `departments` and `offices` are absent.
  - `content` is HTML-escaped (`&lt;div&gt;`). The adapter must `html.unescape` it, then strip tags.
  - Fields: `id` (int), `title`, `absolute_url`, `location.name` (free text), `updated_at`, `first_published`, `company_name`, `requisition_id`.
  - `absolute_url` can point to the company's own site (Datadog) instead of `job-boards.greenhouse.io`.
- **Lever.** Top-level **array**, not an object. `?mode=json` is accepted but is the default: the URL without it returns the same JSON. `limit` works. We did not use `skip`.
  - Fields: `id` (uuid), `text` (title), `hostedUrl`, `applyUrl`, `createdAt` (epoch ms, int), `categories.location`, `categories.allLocations[]`, `country` (ISO-2), `workplaceType`.
  - `workplaceType` values seen: `remote`, `hybrid`, `onsite` (no hyphen).
  - Description: `descriptionPlain` / `description` (HTML), plus `lists[]` (`{text, content}` sections), `additional*` and `opening*`.
  - **A posting can have all description fields blank** (Yuno, third posting). `descriptionBody*` can also be blank while `descriptionPlain` is filled.
- **Ashby.** `.jobs[]` and `apiVersion: "1"`. No pagination, no query params needed.
  - Fields: `id` (uuid), `title`, `jobUrl`, `applyUrl`, `location` (free text), `secondaryLocations[]` (`{location, address}`), `publishedAt` (ISO), `descriptionHtml`, `descriptionPlain`, `isListed`.
  - **`isRemote` and `workplaceType` can be `null`** (3 of 115 for Nubank, 19 of 48 for Supabase). Values seen: `Remote`, `Hybrid`, `OnSite`.
  - `address.postalAddress` can be `{}`. Country is `addressCountry` as a full name (`United States`), not ISO.
  - The empty-board response has no `meta`, only `apiVersion`.

## Empty and 404 behavior

| ATS | Empty board (200) | Unknown slug (404) |
|---|---|---|
| Greenhouse | `{"jobs":[],"meta":{"total":0}}` | `{"status":404,"error":"Job not found"}` |
| Lever | `[]` | `{"ok":false,"error":"Document not found"}` |
| Ashby | `{"jobs":[],"apiVersion":"1"}` | `Not Found` (plain text, **not JSON**) |

Greenhouse and Ashby also return 404 for some slugs that exist elsewhere (`demo` on Greenhouse and Ashby). Lever returns 200 `[]` for `demo`. An adapter must treat 404 as "bad slug", and 200 with an empty list as "no jobs".
