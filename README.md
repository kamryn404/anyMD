# anyMD

Anytype Any-Block v1 export to Obsidian markdown converter. anyMD makes decisions on how to translate Anytype blocks to wikilink style markdown, i.e. a Bookmark block will be rendered as a web hyperlink, an image will be rendered as ![[image.jpg]], note links are rendered as [[wikilinks]], etc.


## Usage

Export an Anytype space/page/collection as Any-Block v1 in JSON format.

`cd` into the root of the exported folder.

Run:

```bash
git clone https://github.com/kamryn404/anyMD
python3 anyMD/convert.py
```

Output is written to `anyMD/output/` by default.

## What it does

- Reads the raw export folders in the parent directory:
  - `objects/`
  - `relations/`
  - `relationsOptions/`
  - `types/`
  - `files/`
  - `filesObjects/`
- Converts Anytype page objects (Any-Block v1) into markdown files.
- Builds YAML frontmatter from `config.yaml`.
- Copies embedded attachments into the configured Obsidian attachments folder.
- Preserves each generated markdown file's modified date from Anytype's system dates on Windows, macOS, and Linux.
- Also preserves creation dates on macOS when the optional `SetFile` tool is available.
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
# notes in collection "Books" would receive frontmatter "in: [[Books]]"
collections:
  - in: "[[$v]]" 
# notes with Anytype cover_image "image.jpg" would recieve frontmatter "cover: [[image.jpg]]" 
cover_image:
  - cover: "[[$v]]" 
properties:
  # notes with type "Journal Entry" would receive frontmatter "in: [[Journal Entry]]"
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
- The generated markdown file gets its filesystem modified date from Anytype by default. Creation dates are also preserved on macOS when `SetFile` is available; Windows and Linux retain their normal filesystem creation dates.
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
<<<<<<< HEAD

## Setup

Use Python 3.10 or newer. From the `anyMD` folder, install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Use `python3` on macOS/Linux if needed, or `py` on Windows. Use the same Python command to install dependencies and run the converter. PyYAML is the only third-party Python dependency.

### File dates

- **All platforms:** The note's modified date is taken from Anytype's `Last modified date`, falling back to `Creation date` if absent.
- **macOS:** Creation dates are also preserved if `SetFile` is available on `PATH` (typically supplied by Apple's developer tools). It is optional.
- **Windows and Linux:** Creation dates are not restored. No macOS tools are required or invoked.
- If an optional tool is missing or a filesystem rejects a timestamp update, conversion continues with a warning. The generated note is kept.

## Usage

From inside the `anyMD` folder:

```bash
python convert.py
```

Or, from the parent export folder, convert everything:

```bash
python3 anyMD/convert.py
```

Convert only notes whose title contains a string:

```bash
python3 anyMD/convert.py --match "Introspection is bad"
```

Output is written to `anyMD/output/` by default.

## Tests

From the `anyMD` folder:

```bash
python -m unittest discover -v
```

Timestamp regression tests use temporary exports and simulate platform/tool failures; they do not modify your exported notes.
=======
>>>>>>> refs/remotes/origin/main
