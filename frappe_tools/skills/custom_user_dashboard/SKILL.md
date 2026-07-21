---
name: custom-user-dashboard
description: Create, list and update HTML "Custom User Dashboards" in this Frappe site as the AI Bot. Use when the user asks you to build a page/dashboard/report view for them or a set of users, publish an HTML view, or manage/disable/delete a dashboard you made.
---

# Custom User Dashboard — AI Bot API

You (the AI Bot) can publish HTML dashboards on this Frappe site. Each dashboard
stores raw HTML plus an access rule, and is rendered to viewers inside a
**sandboxed iframe** (it cannot use a viewer's login session to reach other
data), so bake everything the viewer should see directly into the HTML.

You may only create and manage **your own** dashboards. You have read access to
the rest of the site's data to build them, but you cannot write anything else.

All calls are Frappe REST endpoints:
`POST /api/method/<method>` with `Content-Type: application/json`, authenticated
with your API key/secret (`Authorization: token <key>:<secret>`). The result is
returned under the `message` key.

## Access model

- `require_auth = 1` (default): only the users in `allowed_users` (logged in)
  can open the dashboard.
- `require_auth = 0`: **public** — anyone with the link can open it, no login.
- Public URL of any dashboard: `/user-dashboard?name=<route>`.

## 1. Create — `create_dashboard`

`POST /api/method/frappe_tools.frappe_tools.doctype.custom_user_dashboard.custom_user_dashboard.create_dashboard`

| param | type | required | meaning |
|-------|------|----------|---------|
| `title` | string | yes | display title |
| `html` | string | no | the HTML body to render |
| `require_auth` | 1 or 0 | no (default 1) | 1 = restricted to `allowed_users`; 0 = public link |
| `allowed_users` | list or CSV of user emails | no | who may view it when `require_auth=1` |
| `route` | string | no | URL slug; auto-derived from `title` if omitted |

Returns: `{ name, route, url, title, require_auth, enabled, allowed_users }`
— `url` is the shareable link.

Example body:
```json
{ "title": "Q2 Sales", "html": "<h1>Q2</h1><p>...</p>",
  "require_auth": 1, "allowed_users": ["asha@essdee.fit", "ravi@essdee.fit"] }
```

## 2. List — `list_dashboards`

`POST /api/method/frappe_tools.frappe_tools.doctype.custom_user_dashboard.custom_user_dashboard.list_dashboards`

No params. Returns the dashboards **you own**:
`[ { name, route, url, title, enabled, require_auth, modified }, ... ]`.

## 3. Update / disable / delete — `update_dashboard`

`POST /api/method/frappe_tools.frappe_tools.doctype.custom_user_dashboard.custom_user_dashboard.update_dashboard`

| param | type | meaning |
|-------|------|---------|
| `name` | string (required) | the dashboard's `name`/`route` |
| `html` | string | replace the HTML |
| `title` | string | rename |
| `require_auth` | 1 or 0 | change public/restricted |
| `allowed_users` | list or CSV | **replace** the allowed-users list |
| `disable` | 1 or 0 | `1` = turn off (no one can view it); `0` = re-enable |
| `delete` | 1 | permanently remove it (takes precedence) |

Send only the fields you want to change. Returns the updated
`{ name, route, url, ... }`, or `{ "deleted": <name> }` when `delete=1`.

Examples:
- Update the HTML: `{ "name": "q2-sales", "html": "<h1>updated</h1>" }`
- Disable it: `{ "name": "q2-sales", "disable": 1 }`
- Delete it: `{ "name": "q2-sales", "delete": 1 }`

## Rules & tips

- You can only update/disable/delete dashboards **you created** — attempting
  another owner's dashboard is rejected.
- Put the data INSIDE the HTML; the sandbox blocks the rendered page from
  calling site APIs with the viewer's session.
- For a link you can send to someone, use the returned `url`
  (`/user-dashboard?name=<route>`). Logged-in users can also browse the
  dashboards shared with them at `/app/user-dashboard`.
