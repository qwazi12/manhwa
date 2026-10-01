# Migrating publishing from Outstand to Upload-Post

A hand-off guide written from the Manhwa Recap Studio migration (Sep 30 – Oct 1, 2026).
It covers what was done, the bugs found along the way, and a checklist for doing
the same migration in another project. No secrets are in this file.

**Result:** the first Upload-Post publish (Murim Psychopath ch.44 v5 → Flamingo Remix,
public) completed and is live on YouTube. Outstand is fully removed from the code.

---

## 1. Why we migrated

- **Outstand used a shared YouTube project quota.**
  - Our last Outstand publish failed with a YouTube `429 RESOURCE_EXHAUSTED`, which means quota exceeded.
  - The limit was `defaultVideoInsertPerDayPerProject = 100` on Outstand's Google project, not ours.
  - Other customers' uploads used up our daily limit.
- **Outstand was a two-step flow.** You uploaded media to Outstand's host, then created a post that pointed at the media URL. Every extra hop was another place to fail.
- **Outstand documents no thumbnail field.** Custom thumbnails could not be sent at all.
- **Upload-Post takes the video in one multipart request.**
  - That one request carries per-platform fields and a `thumbnail` file.
  - It runs async with a `request_id` you can poll for the real platform result.

---

## 2. Upload-Post facts you need (verified against the live API)

| Thing | Value |
|---|---|
| API base | `https://api.upload-post.com/api` |
| Auth header | `Authorization: Apikey <UPLOADPOST_API_KEY>` |
| Upload | `POST /upload`, multipart/form-data |
| Required fields | `user` (the profile username), `title`, `platform[]` (repeat per platform), `video` (file) |
| Async | `async_upload=true` → returns `{success, message, request_id, total_platforms}` immediately |
| Status | `GET /uploadposts/status?request_id=<id>` → `{status: "completed", completed, total, results:[{profile_username, platform, success, platform_post_id, post_url, error_message}]}` |
| Profiles / accounts | `GET /uploadposts/users` → `profiles[].username` + `profiles[].social_accounts{network: details}` |
| Connect UI | `POST /uploadposts/users/generate-jwt {username, connect_title}` → `access_url` (fallback: `https://app.upload-post.com/manage-users`) |
| Thumbnail | multipart file field `thumbnail` (or `thumbnail_url`); JPG/PNG, ≤ 2 MB. YouTube applies it only on **verified** channels |
| YouTube fields | `youtube_title`, `youtube_description`, `privacyStatus`, `categoryId`, `selfDeclaredMadeForKids`, `containsSyntheticMedia`, `tags[]` |
| TikTok fields | `tiktok_title`, `privacy_level` (`SELF_ONLY` / `PUBLIC_TO_EVERYONE`), `is_aigc` |
| Instagram fields | `instagram_title`, `media_type=REELS`, `is_ai_generated` |

**The key concept: one request = one profile.** `user` is a single profile. Channels that live
on different Upload-Post profiles must each get their own upload request.

---

## 3. What was done, in order

| # | Step | Commit |
|---|---|---|
| 1 | Created an Upload-Post account; connected the YouTube channels in the Upload-Post dashboard. We ended up with **profiles `mk` (Flamingo Remix) and `default` (Screen Central)** | (dashboard) |
| 2 | Put the API key on Railway as `UPLOADPOST_API_KEY` (secret manager / platform env only, never a file) | (Railway) |
| 3 | Wrote `review_ui/upload_post.py`, the client layer: `config()`, `_request()`, `sync_accounts()`, `accounts_status()`, `get_connect_jwt_url()`, `remove_account()`, `upload_video_post()` (multipart encoder, per-platform fields) | 1cda35d |
| 4 | Added a backend selector in `server.py` and pointed status / connect / refresh / disconnect / eligibility / publish at it | 1cda35d |
| 5 | Accepted several env names (`UPLOADPOST_API_KEY`, `UPLOAD_POST_API_KEY`, `UPLOADPOST_KEY`); default profile `mk` (override `UPLOADPOST_PROFILE`) | 36ceb83 |
| 6 | Replaced the account `<select multiple>` with channel checkboxes inside the Publish card | 2ddfd04 |
| 7 | **Fix:** channels were pre-ticked, so the owner now picks them. **Fix:** the confirm dialog named every account and said "Outstand". **Fix:** an upload to two profiles went to only one of them | c14cf2e |
| 8 | **Removed Outstand completely.** Then: renamed routes `/api/outstand/*` → `/api/publishing/*`; the job now waits for the platform's real result and records the video link; the custom thumbnail is now actually sent; guard tests stop Outstand coming back | this commit |
| 9 | Removed the `OUTSTAND_*` env vars from Railway | (Railway) |

---

## 4. Bugs we hit, so you don't repeat them

1. **Two profiles collapsed into one upload.**
   - The first version looped over targets, kept overwriting `user`, and sent **one** request.
   - With `mk:youtube` and `default:youtube` both picked, only `default` got the video, but both were recorded "published".
   - **Do:** group targets by profile and send one request per profile.
2. **"Accepted" was reported as "published".**
   - With `async_upload=true`, `success: true` only means Upload-Post queued it. YouTube can still reject it (quota, verification, policy).
   - **Do:** store the `request_id` and poll `/uploadposts/status` until `status == "completed"`, then use each `results[].success`, `post_url` and `error_message`.
   - Our job polls for about 15 minutes. If it's still processing, the record stays `in_progress` and the status endpoint re-checks on page load.
3. **Every channel was auto-ticked by default.** That plus auto-save meant every first edit selected all channels. **Do:** default to none and block Publish until at least one is picked.
4. **The confirm dialog listed every connected account**, not the selected ones, and named the old provider. **Do:** build the dialog from the saved targets the server will actually use.
5. **The thumbnail was looked up in the wrong folder.**
   - The job read `<project>/thumbnails/`, but thumbnails are stored in `exports/_thumbs/` (`thumbnail.path_for`), so they were never sent.
   - **Do:** read the path from the same module that saves it.
6. **Stale "not sent" thumbnail copy** was left over from Outstand. Update user-facing wording whenever provider capabilities change.
7. **A fallback provider kept the old system half-alive.**
   - "Use Upload-Post if configured, else Outstand" meant a missing key would silently publish through the old provider.
   - **Do:** have one publisher. If it isn't configured, block publishing with a clear reason (fail loudly).

---

## 5. Checklist for another project

**Setup**
- [ ] Create the Upload-Post account; create one **profile per brand/channel group**; connect each social account inside that profile.
- [ ] Generate an API key and store it as `UPLOADPOST_API_KEY` in the platform's secret store. Add an empty entry to `.env.example`.
- [ ] Decide the default profile (`UPLOADPOST_PROFILE`).
- [ ] If you post to YouTube with custom thumbnails, make sure each channel is **verified**.

**Client layer** (one module, typed I/O, no secrets in logs)
- [ ] `config()`: read the key; fail with the missing variable's name.
- [ ] `sync_accounts()`: `GET /uploadposts/users` → store accounts as `"<profile>:<network>"` with the display name.
- [ ] `upload_video_post(video, metadata, targets_for_ONE_profile, thumbnail)`: multipart, `async_upload=true`.
- [ ] `upload_status(request_id)`: `GET /uploadposts/status`.
- [ ] Connect: `generate-jwt` → redirect, falling back to the dashboard URL.

**Publish job**
- [ ] Re-check eligibility at job start: export exists, approved, metadata valid, targets picked and still connected, not already published.
- [ ] Group targets by profile and send one upload per profile.
- [ ] Record each target as `submitted` with its `request_id`.
- [ ] Poll status (bounded, with backoff), then mark each target `published` (with `post_url`) or `failed` (with `error_message`).
- [ ] Overall status: `published` / `partial` / `failed` / `in_progress`.
- [ ] Never mark "published" on acceptance alone.

**UI**
- [ ] Channel checkboxes, **none ticked by default**; Publish disabled until one is picked.
- [ ] The confirm dialog names exactly the picked channels, the visibility (private/unlisted/public) and the provider.
- [ ] Result rows show the channel name, status, error reason and an **open** link to the live post.
- [ ] Remove every mention of the old provider (labels, dialogs, help text, thumbnail notes).

**Removing the old provider**
- [ ] Delete its client module, callback/OAuth routes, fallback branches and tests.
- [ ] Rename provider-named routes to neutral names (`/api/publishing/*`).
- [ ] Add guard tests: no old-provider routes, module not importable, no old-provider text in the UI.
- [ ] Delete its env vars from the hosting platform. **Revoke its API key in the provider's dashboard.**
- [ ] Leave historical publish records alone; they're history.

**Verify**
- [ ] Unit tests with a mocked HTTP layer only. Never hit the real API from tests.
- [ ] Read-only live check: the accounts endpoint lists every expected channel under the right profile.
- [ ] First real publish: **private**, one channel. Confirm via `/uploadposts/status` that you get `post_url` and that the video plays.
- [ ] Then publish to two channels on **different profiles** and confirm both get their own result row and link.

---

## 6. Where it lives in this repo

- **Client:** `manhwa-recap-v1/review_ui/upload_post.py`
- **Routes and job:** `manhwa-recap-v1/review_ui/server.py`
  - Routes: `/api/publishing/{status,connect,refresh,disconnect,eligibility,publish,publish/status}`
  - Job: `_run_publish_job`, `_resolve_upload_results`, `_publish_overall`
- **UI:** `manhwa-recap-v1/review_ui/review_page.py`, specifically the Publish card, `targetPicker` and `doPublish`.
- **Thumbnails:** `manhwa-recap-v1/review_ui/thumbnail.py`
- **Tests:** `test_upload_post.py`, `test_upload_post_full.py`, `test_mk_profile.py`, `test_publish_backend.py`, `test_publish_targets.py`, `test_thumbnail.py`

## 7. Known gaps (not done yet)

- **Playlist:** our playlist field is not sent. Upload-Post's playlist parameter has not been wired or verified.
- **Scheduling:** `publish_at` is not sent to Upload-Post; publishing is immediate.
- **Old state files:** the Outstand files on the data volume (`_outstand_accounts.json`, `_outstand_state.json`) are now inert and were left in place.
