# frappe_tools → Frappe v16: roles & permissions changes

*Pre-coding research report (verified against frappe 15.107.5 vs 16.18.2 on disk). No implementation code — analysis only, requested before any porting.*

---

## 1. How v16 handles roles & permissions (what changed from v15)

Only the framework-level differences that actually touch how permissions resolve. Unchanged surface (public `frappe.has_permission()` wrapper, `frappe.get_roles()`, `@frappe.whitelist`, the `permission_query_conditions` hook call convention, `add_permission`/`setup_custom_perms`/`add_user_permission` signatures, the Custom-DocPerm wholesale-override rule, Role/Has Role field sets) is omitted.

### 1.1 🔴 `has_permission` controller-hook contract: `None` no longer means "no opinion" — it now DENIES (BREAKING)
The only change that breaks frappe_tools. **Verified directly.**
- **v15** (`frappe/permissions.py:456-457`): `if controller_permission is not None: return bool(controller_permission)`. A hook returning `None` is skipped (neutral); if all hooks return `None`, default is allow.
- **v16** (`frappe/permissions.py:495-496`): `if not controller_permission: return bool(controller_permission)`. Any falsy return (`None`/`False`/`0`/`''`) immediately returns `False` → DENY. Only a truthy return lets the loop continue. Docstring (`:484`): *"Controllers can only deny permission, they can not explicitly grant…"*
- **Source of intent**: commit `5ef8577cff` — *"fix!: Stricter requirement for permission hooks (#24253)"*, BREAKING: *"hook need to explicitly return True (or truthy) value to allow user. They will be blocked otherwise."*

### 1.2 🟡 Single doctypes now run the controller hook
- **v16** (`permissions.py:134`): `if not doc and meta.issingle: doc = meta.name` — Singles now flow into `get_doc_permissions` → `has_controller_permissions`. Widens 1.1's blast radius to Singles (e.g. AI Bot Settings). Same fix covers it.

### 1.3 🟢 New `mask` permission column on DocPerm and Custom DocPerm
- **v16**: both `DocPerm` and `Custom DocPerm` gain `mask: DF.Check` (default `0`) — field-level masking via `meta.get_masked_fields()` / `get_permlevel_access(permission_type="mask")`. Not in the std `rights` tuple. (v16 `core/doctype/docperm/docperm.json`, `docperm.py:23`, `custom_docperm.py:24`, `model/meta.py:209-211,721-729`.)

### 1.4 🟢 Internal kwarg rename `raise_exception` → `print_logs`
- Internal `frappe.permissions.has_permission` (`v15:82` → `v16:87`, keyword-only) + `has_child_permission`. Public `frappe.has_permission()` wrapper signature is byte-identical (still `throw=`). frappe_tools never calls the internal fn — noted only so nobody adds a `raise_exception=` kwarg during the port.

### 1.5 🟢 `select` ptype now implied by `read`
- **v16** (`permissions.py:212-223`): if `ptype == 'select'` and not yet permitted, re-checks `read`. Makes the AI Bot seed's explicit `select` grant redundant (harmless).

### 1.6 🟢 New `Permission Type` doctype + `rights`→`std_rights` rename
- **v16**: new `core/doctype/permission_type/`; `permissions.py` iterates `get_rights(doctype)` (= `std_rights` + custom); constant renamed `std_rights` with `rights = std_rights` back-compat alias. 14 standard ptypes unchanged. Inert for frappe_tools.

### 1.7 🟢 Other internals (no frappe_tools surface)
`reset_perms` deletes Custom DocPerm via `delete_doc(force=True)`; `update_permission_property` drops `if_owner` from lookup, routes via `custom_docperm.update_custom_docperm`; `has_user_permission` `ptype` keyword-only; `frappe.only_for()` dropped its `in_test` bypass; multi-role-profile support (`User Role Profile` child + `role_profiles` MultiSelect; legacy `role_profile_name` kept); desk base route `/app` → `/desk` (frontend redirect). None called by frappe_tools' permission layer.

---

## 2. frappe_tools impact — file by file

| File / site | Current | v16 behavior | Required change | Severity |
|---|---|---|---|---|
| `permissions.py:24-34` `ai_bot_has_permission` | Returns `True` for AI Bot read-style ptypes; **`None`** otherwise | Wired `"*"`, fires on every doc-level check (+ Singles, §1.2). Every `None` → explicit DENY | **MUST FIX** — never return falsy. Return `True` on grant **and** as the neutral pass-through (a `"*"` controller hook can only deny; granting stays in DocPerm seeding) | 🔴 |
| `permissions.py:8-14` comment | "belt-and-braces… definite True" | A `True` can never grant in v16, only fail-to-deny | Update comment to avoid misleading future readers | 🟢 |
| `permissions.py:37-47` `ai_bot_query_conditions` | `''` for AI Bot, `None` otherwise | Hook call convention identical (legacy `db_query.py` + new `database/query.py`); `if c:=` skips falsy | None | 🟢 |
| `permissions.py:32,45` `frappe.get_roles(user)` | AI Bot membership test | Identical | None | 🟢 |
| `hooks.py:120-126` wildcard hooks | `has_permission`/`permission_query_conditions` `{"*": …}` | `"*"` merge unchanged; the `"*"` on has_permission is *why* 1.1 is site-wide | No hooks.py change; fix is in the handler | 🔴 (via handler) |
| `setup/ai_bot_permissions.py` `_CANDIDATE_PERM_FIELDS` (~160-172) | Intersects candidate tuple w/ live DocPerm columns | New `mask` not in list → never mirrored (defaults 0); no crash | **Optional:** add `"mask"` so `_mirror_standard_into_custom` faithfully copies a standard row with `mask=1` | 🟡 |
| `setup/ai_bot_permissions.py` seeding (`db_insert`/`db.delete`) | Plants DocPerm/Custom DocPerm/Has Role rows | Autonaming, `DF.Check` defaults, `db_insert`/`db.delete` unchanged; `mask` takes DB default 0 | None | 🟢 |
| `setup/ai_bot_permissions.py:13` `_AI_BOT_PTYPES` incl `'select'` | Seeds `select` | `read` implies `select` (§1.5) | Optional cleanup | 🟢 |
| `setup/ai_bot_permissions.py` `ensure_role_exists` / Has Role `parentfield='roles'` | Creates Role, inserts Has Role | Role/Has Role field sets unchanged | None | 🟢 |
| `setup/repair_docperm_override.py` + `verify_perm_safety.py` + `_mirror_standard_into_custom` (2026-05-29 outage patch) | Mirrors standard rows before override | Custom-DocPerm wholesale-override rule byte-identical in v16 | **KEEP** — outage root cause persists in v16 | 🟢 (keep) |
| `hooks.py:13-22` commented `add_to_apps_screen` + `has_app_permission` (file absent) | Inactive | Callback contract unchanged: zero-arg callable → truthy/falsy | None now; if enabled later keep zero-arg | 🟢 |
| Direct `frappe.has_permission(...)` — `api/doc_extract.py:28`, `api/doc_resolve.py:18`, `api/doc_scanner.py:211,231,424,444`, `extractors/base.py:77` | Public wrapper, some pass `doc=<str>` | Wrapper byte-identical; string `doc` resolved via `get_lazy_doc` (perf win); `print_logs` rename internal | None | 🟢 |
| `api/doc_scanner.py` 8 `@frappe.whitelist(allow_guest=True)` | Guest endpoints | `whitelist` identical | None (guest exposure is a security-posture question, not a port issue) | 🟢 |

---

## 3. Required changes (ordered, no code — what & why)

1. **Fix `ai_bot_has_permission` to never return a falsy value (🔴).** The one mandatory, install-blocking change. As written it returns `None` for every non-AI-Bot user and every non-read ptype; under v16's deny-on-falsy contract that denies doc-level access for effectively every non-Administrator user on every doctype (total lockout). Make it return `True` on the AI Bot grant case **and** `True` as the neutral "don't deny" for everyone/everything else. The real grant keeps coming from the seeded DocPerm/Custom DocPerm rows (a `"*"` controller hook can only deny in v16). Forward-compatible: in v15 a truthy controller return also meant "don't deny," so one codebase serves both.
2. **Add `"mask"` to `_CANDIDATE_PERM_FIELDS` (🟡, optional/defensive).** Without it, `_mirror_standard_into_custom` copies a standard row with `mask=1` into Custom DocPerm as `mask=0`, silently dropping v16 field-masking when a doctype is pushed into override mode. AI Bot's own rows stay `mask=0` (no `_AI_BOT_PTYPES` change).
3. **Update the misleading comment in `permissions.py:8-14` (🟢).**
4. **Keep the DocPerm-override mirror/monkey-patch (🟢 do-not-remove).** Override rule identical in v16; the 2026-05-29 outage mitigation remains required.
5. **Optional cleanup:** drop redundant `'select'` seed; leave all direct `has_permission`/`get_roles`/`whitelist` call sites untouched.

---

## 4. Open questions / decisions

1. **One codebase for v15+v16, or a v16 branch?** The mandatory fix is forward-compatible (returning `True` is correct under both), and the `mask` addition is naturally inert on v15 (column absent → dropped by the schema intersection). **Recommend: single codebase, no version guards.**
2. **AI Bot DocPerm monkey-patch under v16:** override rule unchanged → keep the existing mirror approach as-is (this report assumes yes).
3. **`mask` fidelity:** only matters if a doctype AI Bot pushes into override mode uses v16 field-masking (none today). Include now for future-proofing, or defer?
4. **(Not permissions) `/app`→`/desk`:** `DocScannerController.vue:829`, `MainLayoutHandler.vue:216` do `replaceState('/app/…')`. Cosmetic on v16 (hard refresh redirects cleanly). Fold into this PR or handle separately?
5. **(Security posture, not porting) 8 guest scanner endpoints** behave identically on v16 — revisit guest exposure now or leave?

---

## 5. Verdict

**Not v16-ready as-is** — it will cause a **site-wide lockout** on install (the `"*"` `ai_bot_has_permission` hook returns `None` for the vast majority of checks, and v16 treats every falsy controller return as a hard DENY).

**But the fix is tiny and the rest is clean.** Of the whole permission inventory, exactly **one function needs a behavioral change** (~a few lines: return `True` instead of `None` on fall-through). One optional one-line addition (`"mask"`) + one comment update. Every public API frappe_tools uses has an identical v16 signature; query-conditions hook unaffected; seeding mechanism unchanged; outage-mitigation patch stays valid.

**Rough size:** ~1 hour code + a v16 smoke test (install on a v16 site; confirm a non-Admin non-AI-Bot user can still read/write a normal doctype, and AI Bot still gets its seeded grants). **Effort: small. Risk if shipped unfixed: catastrophic.**
