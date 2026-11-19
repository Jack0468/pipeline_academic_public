Convert the attached pages of a study document for {unit_code} ({unit_name}) into Markdown, faithfully.

The document is third-party study material (a formula sheet or condensed notes). It becomes the backbone of the
student's exam handbook, so accuracy matters more than anything else.

Rules:
- Reproduce the content exactly: every formula, condition, symbol definition, unit, table row and page or slide
  reference (for example "[L7, slide 12]" or "p. 34"). Don't add, correct, explain or summarise anything. If
  something looks wrong, keep it as written and add `<!-- check: ... -->` after it.
- Mathematics in LaTeX: `$...$` inline, `$$...$$` for display. Subscripts and superscripts as in the original.
- Tables stay tables (Markdown pipe tables). Keep the original column headings.
- Keep the original headings and their order, as `##` and `###`.
- Mark anything unreadable as [unreadable].
- No front matter, no introduction, no emojis. Output only the Markdown.
