# Osho Vigyan autopost

Posts one Vigyan Bhairav Tantra carousel a day to @oshovigyan at about 7:30 PM IST, in order, until technique 112. Runs on GitHub's servers, so no computer needs to be on.

**How it works.** Each post lives in `queue/NNN/` (slides `1.jpg` to `4.jpg` plus `post.json` with caption and alt text). Every day the workflow in `.github/workflows/post.yml` takes the lowest number not yet in `state/posted.json`, publishes it through the Instagram API, and records it.

**Pause or resume.** Set `"enabled"` in `config.json` to `false` or `true`.

**Test without posting.** Actions tab, "Daily Instagram post", Run workflow, mode `test`.

**Secrets** (Settings, Secrets and variables, Actions): `IG_TOKEN` is the Instagram long-lived token, `TOKEN_KEY` is any long password. The token is refreshed weekly and stored encrypted in `state/token.enc`.

**If something fails,** GitHub emails the repository owner. When fewer than 3 posts are left, an issue is opened as a reminder.
