# anyMD

Small Anytype Anyblock export to Obsidian markdown converter.

## What it does

- Reads the raw export folders in the parent directory:
  - `objects/`
  - `relations/`
  - `relationsOptions/`
  - `types/`
  - `files/`
  - `filesObjects/`
- Converts Anytype page objects into markdown files.
- Builds YAML frontmatter from `config.yaml`.
- Copies embedded attachments into the configured Obsidian attachments folder.
- Sets each generated markdown file's creation date and modified date from Anytype's system dates.
- Converts links to Anytype bookmark objects into normal markdown web links.

## Config format

`config.yaml` lets you decide:

- how dates should be formatted
- where copied attachments should go
- how attachment paths should be rendered
- which Anytype object types should be skipped
- how Anytype collection membership should map into Obsidian frontmatter
- how note cover images should map into Obsidian frontmatter
- how Anytype properties map into Obsidian frontmatter

Example:

```yaml
date_format: "YYYY-MM-DD"
attachments_folder: "attachments"
attachment_link_style: "relative"
exclude_types:
  - "Bookmark"
collections:
  - in: "[[$v]]"
cover_image:
  - cover: "[[$v]]"
properties:
  Object type:
    - in: "[[$v]]"
  Date:
    - date: $v
  Journal Type:
    - tags: $v
```

### Property mapping rules

- Each key under `properties` is an Anytype property to read.
- Each list item maps that Anytype property into one Obsidian frontmatter key.
- `$v` is replaced with the resolved Anytype property value.
- `$v`, `"$v"`, and `'$v'` are all valid and the output keeps that quote style.
- For normal properties, `"[[$v]]"` or `'[[$v]]'` produces an Obsidian wikilink list, even for a single value.
- If the Anytype property resolves to multiple values, the Obsidian property becomes a YAML list.
- `Creation date` and `Last modified date` are not added to frontmatter unless you explicitly map them here.
- The generated markdown file itself still gets its filesystem creation date and modified date from Anytype by default.
- `exclude_types` skips generating markdown files for Anytype objects whose type name matches exactly.
- `collections` maps each note's containing Anytype collection names into frontmatter.
- Scalar frontmatter values keep the quote style from your template when possible.

### Attachment path style

`attachment_link_style` controls how copied attachment paths are written:

- `relative` writes paths like `attachments/room_magic.jpg`
- `filename only` writes paths like `room_magic.jpg`

This affects embedded media, normal attachment links, and `cover_image` frontmatter values.

### How repeated mappings behave

When more than one mapping writes to the same frontmatter key, the converter does one of two things:

- If the rendered value is a YAML list, the values are appended.
- If the rendered value is a scalar value, it replaces whatever value was already there.

In practice, that means:

- `"[[$v]]"` appends values, because it renders as a list for normal properties.
- `$v` replaces the previous value for that frontmatter key.

Example: two Anytype properties can both feed the same `in` frontmatter key:

```yaml
properties:
  Object type:
    - in: "[[$v]]"
  Journal Type:
    - in: "[[$v]]"
```

This becomes:

```yaml
in:
  - "[[Journal Entry]]"
  - "[[Freewriting]]"
```

Example: one date field can fall back to another:

```yaml
properties:
  Creation date:
    - date: $v
  Date:
    - date: $v
```

This means:

- every note first gets `date` from `Creation date`
- if a note also has `Date`, that later scalar mapping replaces the earlier `date` value

So config order matters:

- later scalar mappings override earlier scalar mappings
- later list mappings append to the existing list
- a later scalar mapping can also replace an earlier list value if you intentionally map them to the same key

This also means:

```yaml
From:
  - in: "[[$v]]"
```

becomes:

```yaml
in:
  - "[[Daily Notes]]"
  - "[[Journal]]"
```

Collection membership works the same way:

```yaml
collections:
  - in: "[[$v]]"
```

If a note belongs to `Learning`, that collection name is added to the note's `in` frontmatter. If `in` already has values from normal property mappings, the collection wikilinks are appended to the same list.

Cover images can also be mapped into frontmatter:

```yaml
cover_image:
  - cover: "[[$v]]"
```

This copies the Anytype cover image into your attachments folder and makes `$v` the relative attachment path, for example:

```yaml
cover: "[[attachments/room_magic.jpg]]"
```

If `attachment_link_style` is set to `filename only`, the same mapping would become:

```yaml
cover: "[[room_magic.jpg]]"
```

And a single-value wikilink mapping is also emitted as a list:

```yaml
Object type:
  - in: "[[$v]]"
```

becomes:

```yaml
in:
  - "[[Page]]"
```

### How source properties are matched

Source property names are case-sensitive and should be written exactly as they appear in Anytype.

- `Journal Type` matches `Journal Type`
- `Creation date` matches `Creation date`
- `Object type` matches `Object type`

Do not convert spaces to underscores.

This is valid YAML and does not need quotes:

```yaml
Journal Type:
  - in: "[[$v]]"
```

Quotes are optional for keys like this.

## Usage

Convert everything:

```bash
python3 anyMD/convert.py
```

Convert only notes whose title contains a string:

```bash
python3 anyMD/convert.py --match "Introspection is bad"
```

Output is written to `anyMD/output/` by default.
