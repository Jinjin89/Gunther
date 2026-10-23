# Style: Light
Mood: calm, editorial, credible. Good for research, reports, teaching, policy briefings.

```css
.slide { --bg:#FFFFFF; --text:#1B1F24; --muted:#5F6672; --panel:#F3F1EC; --line:#E3E0D8;
  --dominant:#1F2A44; --accent:#E4572E; --accent-soft:#FBE3DA; --positive:#2A9D8F;
  --dark-bg:#1F2A44; --on-dark:#FFFFFF; --on-dark-muted:#B4BCCB;
  --font-head:"Source Serif 4","Noto Serif CJK SC","Songti SC",serif;
  --font-body:"Inter","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif; --head-weight:600; }
```

Palette use: white pages; deep ink navy (--dominant) carries about 60% of the visual weight
(cover, section slides, big numbers, chart bars); vermilion (--accent) is the one sharp accent
(the highlighted bar, the key number, a marker); warm grey panels (--panel) group content.
Cover and section slides: class "slide dark" (navy background, white serif title).

Motif (repeat on every content slide): a small vermilion square (14×14 px) before a short
uppercase eyebrow label above the title, e.g.
<div style="display:flex;align-items:center;gap:10px;font-size:18px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)"><span style="width:14px;height:14px;background:var(--accent)"></span>The energy number</div>

Typography: titles 40–46 px serif; body 20–24 px sans; big numbers 110–150 px serif in
--dominant or --accent; captions 18 px --muted.
Set a big number's unit (%, ×, $) in the body font at about 45% of the number's size: the serif
"%" looks clumsy next to a large figure.

Sample snippets:
- Big-number slide: left 55% a huge number + one-line label + one context line; right 45% a
  --panel card with 2–3 short supporting facts.
- Cards: 2–4 equal cards on --panel with a number badge (40 px circle, --dominant fill, white
  numeral) and a bold lead + one line.
