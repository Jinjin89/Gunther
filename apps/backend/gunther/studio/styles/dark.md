# Style: Dark
Mood: confident, high-stakes, cinematic. Good for board and investor decks, keynotes, strategy.

```css
.slide { --bg:#0E1116; --text:#F2F4F7; --muted:#A3ACBA; --panel:#181D26; --line:#2A313D;
  --dominant:#F2F4F7; --accent:#F2C14E; --accent-soft:#3A3220; --positive:#3DDC97;
  --dark-bg:#06080B; --on-dark:#FFFFFF; --on-dark-muted:#A3ACBA;
  --font-head:"Inter","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
  --font-body:"Inter","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif; --head-weight:700; }
```

Palette use: near-black pages throughout; white type; amber (--accent) is the single accent
(key numbers, the highlighted bar, icons); mint (--positive) only for a good-news number;
panels (--panel) with a 1 px --line border group content. Cover and section slides use class
"slide dark" (even darker) with a huge title.

Motif (every content slide): a large faint ring (a 420 px circle with a 3 px border of
rgba(255,255,255,0.06), no fill) bleeding off the top-right corner behind the content.

Typography: titles 40–48 px bold, tight; body 20–23 px; big numbers 120–170 px bold in
--accent; captions 18 px --muted.

Sample snippets:
- Stat row: 2–4 columns, each a 72–96 px amber number over a 18 px muted label, separated by
  1 px --line verticals.
- Split: left a big claim in 34 px; right a --panel card with the evidence list.
