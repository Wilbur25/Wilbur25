# Squarespace update checklist — williamgross.co

Squarespace 7.1 menu names change now and then. If a label below doesn't match what you see, look for the closest one.

## Part 1 — Quick fixes (~15 min)

### 1. Social links (footer currently points to Squarespace's own accounts)
1. Log in, open your site, scroll to the footer, click **Edit Footer**.
2. Click the Instagram/LinkedIn/Twitter links block, then the pencil icon.
3. Replace each URL with your own profile, or delete the ones you don't use.
   - If the block pulls links from site settings, update them under **Settings → Social Links** instead.
4. **Save**.

### 2. Footer "HOME" link
The footer HOME link goes to `/sereno-sofia-parazi-photography`. That is very likely your **homepage's own leftover template URL**, not a separate page, so **do not delete that page**.
1. In **Edit Footer**, click the HOME link, then change its URL to `/`.
2. Optional cleanup: **Pages** → hover your homepage → gear icon → **General** → change **URL Slug** to `home` → **Save**.

### 3. Broken Kasling VTOL button
1. **Pages** → find the Kasling page in the list (may be under "Not Linked"). Click the gear icon and copy its **URL Slug**.
   - If there's no Kasling page, it was deleted. Hide the card for now and we'll rebuild it later.
2. Open **Portfolio** → **Edit** → click the Kasling card's **Project Gallery** button → pencil icon → set the link to `/<that-slug>` → **Save**.

### 4. Remove the broken newsletter block
1. **Edit Footer** → click the "Subscribe" newsletter block → trash icon → **Save**.

### 5. Browser-tab titles (every project page says "Gallery 1")
For each project page: **Pages** → hover page → gear icon → **SEO** tab → set **SEO Title**:
| Page | SEO Title |
|---|---|
| Common Objects (`/robotic`) | Rugged Robotics Enclosures — Common Objects — William Gross |
| NovaWurks (`/novawurks-satellites`) | Satellite & Robotic Arm Design for DARPA and NASA — William Gross |
| Kasling | VTOL & Fixed-Wing Drone Design — Kasling Aircraft — William Gross |
| Personal Projects | Personal Projects — William Gross |
| Modus (`/modus-motorcycle`) | Electric Motorcycle Conversion — Modus — William Gross |

Also check the **Navigation Title** on the General tab while you're there. That's the name shown in menus.

## Part 2 — NovaWurks page (~20–30 min)
Text to paste is in `portfolio/novawurks.md`.

1. **Pages** → NovaWurks → gear → **SEO** → paste SEO Title + SEO Description → **Save**.
2. Open the page → **Edit**. Keep the existing gallery section; everything below goes **above** it.
3. **Top banner:** **Add Section** (above the gallery) → blank section → section pencil icon → **Background** → add your best satellite photo. Add a **Text** block with the title (Heading 1) and the bold one-liner. If the text is hard to read, raise the image overlay/darkness in the section's background settings.
4. **Facts row:** **Add Section** → blank → add 5 Text blocks side by side (drag them into one row). Each block has a small bold label (Company / Role / Programs / Years / Tools) and the value under it.
5. **Challenge / Design / Build & Test / Result:** for each one, **Add Section** → blank → Text block (Heading 2 + paragraph) on one side, Image block on the other. **Alternate sides** (text left/photo right, then photo left/text right).
6. **Gallery:** add a Heading 2 "More Photos" above the existing gallery.
7. **Bottom nav:** **Add Section** → two **Button** blocks: "← Common Objects Robotics" → `/robotic` and "Kasling Aircraft VTOL →" → (Kasling slug).
8. **Alt text:** click each image → pencil → fill in the **Alt text** / description with a short real description (e.g. "Assembled HISat satellite module on a test stand").
9. **Save**, then check it on your phone. Squarespace has a mobile preview icon at the top of the editor.

## Part 3 — When done
Tell Claude what looked off and it'll adjust. Next up: Common Objects page and the new homepage layout.
